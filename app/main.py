from typing import Annotated, List, Optional
from jose import JWTError, jwt
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form, Query, Header
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import Session, select, desc
import shutil
import os
import random
import subprocess
import time
from uuid import UUID, uuid4

from database import engine, get_session, init_db
from data_models import User, UserCreate, UserRead, UserLogin, Token, Video, VideoRead, Category, VideoUpdate, UserUpdate, UserFollow, UserBlock, UserPasswordUpdate, EmailUpdate, UserReadProfile, VideoLike
from security import get_password_hash, verify_password, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES, SECRET_KEY, ALGORITHM
from tasks import transcode_video_task
from init_data import init_categories

app = FastAPI(title="MyVideo Backend", version="1.7.0")
app.mount("/static", StaticFiles(directory="/data/myvideo/static"), name="static")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

@app.on_event("startup")
def on_startup():
    init_db()
    os.makedirs("/data/myvideo/static/videos/uploads", exist_ok=True)
    os.makedirs("/data/myvideo/static/videos/processed", exist_ok=True)
    os.makedirs("/data/myvideo/static/thumbnails", exist_ok=True)
    os.makedirs("/data/myvideo/static/thumbnails/temp", exist_ok=True) # 临时目录
    os.makedirs("/data/myvideo/static/avatars", exist_ok=True)
    init_categories()

async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)], session: Session = Depends(get_session)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None: raise credentials_exception
    except JWTError: raise credentials_exception
    user = session.exec(select(User).where(User.username == username)).first()
    if user is None: raise credentials_exception
    return user

async def get_optional_current_user(token: Annotated[Optional[str], Depends(oauth2_scheme)] = None, session: Session = Depends(get_session)):
    if token is None:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None: return None # Token无效，但我们不抛异常
    except JWTError:
        return None # Token无效，但我们不抛异常
    user = session.exec(select(User).where(User.username == username)).first()
    return user

# ... (省略 upload, get_videos 等未修改的接口)
@app.post("/videos/upload", response_model=VideoRead)
async def upload_video(
    title: str = Form(...),
    description: str = Form(None),
    category_id: int = Form(None),
    tags: str = Form(""),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    if not file.content_type.startswith("video/"): raise HTTPException(status_code=400, detail="File must be a video")
    file_id = uuid4()
    ext = os.path.splitext(file.filename)[1]
    save_filename = f"{file_id}{ext}"
    save_path = f"/data/myvideo/static/videos/uploads/{save_filename}"
    with open(save_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    new_video = Video(id=file_id, title=title, description=description, category_id=category_id, tags=tag_list, user_id=current_user.id, original_file_path=save_path, status="pending")
    session.add(new_video)
    session.commit()
    session.refresh(new_video)
    transcode_video_task.delay(str(new_video.id))
    return new_video

@app.get("/videos", response_model=List[VideoRead])
def get_videos(session: Session = Depends(get_session), page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), category_id: Optional[int] = Query(None), keyword: Optional[str] = Query(None), sort_by: str = Query("latest", enum=["latest", "popular"])):
    statement = select(Video).where(Video.status == "completed").where(Video.visibility == "public")
    if category_id: statement = statement.where(Video.category_id == category_id)
    if keyword: statement = statement.where(Video.title.contains(keyword))
    if sort_by == "latest": statement = statement.order_by(desc(Video.created_at))
    elif sort_by == "popular": statement = statement.order_by(desc(Video.views))
    offset = (page - 1) * size
    return session.exec(statement.offset(offset).limit(size)).all()

@app.get("/videos/{video_id}", response_model=VideoRead)
def read_video(
    video_id: str,
    session: Session = Depends(get_session),
    authorization: Optional[str] = Header(None) # 从Header手动获取Authorization
):
    current_user = None
    if authorization:
        token = authorization.replace("Bearer ", "")
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username: str = payload.get("sub")
            if username:
                current_user = session.exec(select(User).where(User.username == username)).first()
        except JWTError:
            pass # token无效, current_user保持为None
    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404, detail="Video not found")

    # 统计点赞和踩的数量
    likes_count = session.exec(
        select(VideoLike).where(VideoLike.video_id == video_id, VideoLike.like_type == "like")
    ).all().__len__() # 使用 len()

    dislikes_count = session.exec(
        select(VideoLike).where(VideoLike.video_id == video_id, VideoLike.like_type == "dislike")
    ).all().__len__() # 使用 len()

    is_liked_by_current_user = False
    is_disliked_by_current_user = False

    if current_user:
        user_like = session.exec(
            select(VideoLike).where(VideoLike.video_id == video_id, VideoLike.user_id == current_user.id)
        ).first()
        if user_like:
            if user_like.like_type == "like":
                is_liked_by_current_user = True
            elif user_like.like_type == "dislike":
                is_disliked_by_current_user = True
    
    # 增加视频浏览数 (如果有需要可以加)
    # video.views += 1
    # session.add(video)
    # session.commit()
    # session.refresh(video)

    return VideoRead(
        id=video.id,
        title=video.title,
        description=video.description,
        status=video.status,
        visibility=video.visibility,
        processed_file_path=video.processed_file_path,
        thumbnail_path=video.thumbnail_path,
        duration=video.duration,
        views=video.views,
        complete_views=video.complete_views,
        progress=video.progress,
        created_at=video.created_at,
        tags=video.tags,
        owner=UserRead.from_orm(video.owner), # 使用 from_orm 来填充 UserRead
        category=video.category,
        likes_count=likes_count,
        dislikes_count=dislikes_count,
        is_liked_by_current_user=is_liked_by_current_user,
        is_disliked_by_current_user=is_disliked_by_current_user,
    )

@app.post("/videos/{video_id}/like")
async def like_video(
    video_id: UUID,
    like_type: str, # "like" or "dislike"
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    if like_type not in ["like", "dislike"]:
        raise HTTPException(status_code=400, detail="Invalid like type")

    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # 检查用户是否已有点赞或踩
    existing_like = session.exec(
        select(VideoLike)
        .where(VideoLike.user_id == current_user.id, VideoLike.video_id == video_id)
    ).first()

    if existing_like:
        if existing_like.like_type == like_type:
            # 已经点过赞/踩，再次点击表示取消
            session.delete(existing_like)
            session.commit()
            return {"status": "unliked", "like_type": like_type}
        else:
            # 改变点赞/踩类型
            existing_like.like_type = like_type
            session.add(existing_like)
            session.commit()
            session.refresh(existing_like)
            return {"status": "changed", "like_type": like_type}
    else:
        # 新增点赞或踩
        new_like = VideoLike(user_id=current_user.id, video_id=video_id, like_type=like_type)
        session.add(new_like)
        session.commit()
        session.refresh(new_like)
        return {"status": "liked", "like_type": like_type}

@app.delete("/videos/{video_id}/like")
async def unlike_video(
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    existing_like = session.exec(
        select(VideoLike)
        .where(VideoLike.user_id == current_user.id, VideoLike.video_id == video_id)
    ).first()

    if existing_like:
        session.delete(existing_like)
        session.commit()
        return {"status": "unliked", "like_type": existing_like.like_type}
    else:
        raise HTTPException(status_code=404, detail="Not liked or disliked by user")

@app.get("/categories", response_model=List[Category])
def get_categories(session: Session = Depends(get_session)):
    return session.exec(select(Category)).all()

@app.get("/users/{user_id}/profile", response_model=UserReadProfile)
async def get_user_profile(
    user_id: UUID, # 使用 UUID 类型
    session: Session = Depends(get_session),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    is_following = False
    if current_user and current_user.id != user_id:
        follow_entry = session.exec(
            select(UserFollow)
            .where(UserFollow.follower_id == current_user.id)
            .where(UserFollow.followed_id == user_id)
        ).first()
        if follow_entry:
            is_following = True

    # 查询粉丝数
    followers_count = len(session.exec(
        select(UserFollow)
        .where(UserFollow.followed_id == user_id)
    ).all())

    # 查询关注数
    following_count = len(session.exec(
        select(UserFollow)
        .where(UserFollow.follower_id == user_id)
    ).all())

    # 查询视频投稿数
    video_count = len(session.exec(
        select(Video)
        .where(Video.user_id == user_id)
        .where(Video.visibility == "public") # 只计算公开视频
    ).all())

    return UserReadProfile(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        created_at=user.created_at,
        avatar_path=user.avatar_path,
        bio=user.bio,
        is_following=is_following,
        followers_count=followers_count,
        following_count=following_count,
        video_count=video_count,
    )

@app.get("/users/me/following", response_model=List[UserRead])
async def get_followed_users(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    following_entries = session.exec(
        select(UserFollow).where(UserFollow.follower_id == current_user.id)
    ).all()
    followed_users = []
    for entry in following_entries:
        user = session.get(User, entry.followed_id)
        if user:
            followed_users.append(UserRead(
                id=user.id,
                username=user.username,
                email=user.email,
                is_active=user.is_active,
                created_at=user.created_at,
                avatar_path=user.avatar_path,
                bio=user.bio,
            ))
    return followed_users

@app.put("/users/me", response_model=UserRead)
async def update_my_profile(
    user_in: UserUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    for key, value in user_in.dict(exclude_unset=True).items():
        setattr(current_user, key, value)
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return current_user

@app.put("/users/me/password", status_code=status.HTTP_200_OK) # 改为200 OK
async def change_my_password(
    password_in: UserPasswordUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    if not verify_password(password_in.old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="旧密码不正确")
    current_user.hashed_password = get_password_hash(password_in.new_password)
    session.add(current_user)
    session.commit()
    return {"message": "密码修改成功"}

@app.post("/users/avatar", response_model=UserRead)
async def upload_my_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="文件必须是图片")
    
    # 构建头像保存路径
    avatar_filename = f"{current_user.id}.png" # 假设都保存为png
    avatar_path = f"/data/myvideo/static/avatars/{avatar_filename}"
    
    with open(avatar_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    current_user.avatar_path = f"/static/avatars/{avatar_filename}"
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return current_user

@app.get("/users/me/blocks", response_model=List[UserRead])
async def get_my_blocks(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    blocked_entries = session.exec(
        select(UserBlock).where(UserBlock.blocker_id == current_user.id)
    ).all()
    blocked_users = []
    for entry in blocked_entries:
        user = session.get(User, entry.blocked_id)
        if user:
            blocked_users.append(UserRead(
                id=user.id,
                username=user.username,
                email=user.email,
                is_active=user.is_active,
                created_at=user.created_at,
                avatar_path=user.avatar_path,
                bio=user.bio,
            ))
    return blocked_users

@app.post("/users/{user_id}/block")
def block_user(user_id: UUID, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="不能拉黑自己")
    existing_block = session.exec(select(UserBlock).where(UserBlock.blocker_id == current_user.id, UserBlock.blocked_id == user_id)).first()
    if not existing_block:
        session.add(UserBlock(blocker_id=current_user.id, blocked_id=user_id))
        session.commit()
    return {"ok": True}

@app.delete("/users/{user_id}/block")
def unblock_user(user_id: UUID, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    existing_block = session.exec(select(UserBlock).where(UserBlock.blocker_id == current_user.id, UserBlock.blocked_id == user_id)).first()
    if existing_block:
        session.delete(existing_block)
        session.commit()
    return {"ok": True}

@app.get("/users/{user_id}/videos/public", response_model=List[VideoRead])
def get_user_public_videos(user_id: UUID, session: Session = Depends(get_session)):
    videos = session.exec(
        select(Video)
        .where(Video.user_id == user_id)
        .where(Video.visibility == "public")
        .where(Video.status == "completed")
        .order_by(desc(Video.created_at))
    ).all()
    user_exists = session.get(User, user_id)
    if not user_exists:
        raise HTTPException(status_code=404, detail="User not found")
    return videos

# --- 创作者 API 修改 ---

class VideoUpdateExt(VideoUpdate):
    temp_thumbnail_path: Optional[str] = None

@app.put("/videos/{video_id}", response_model=VideoRead)
def update_video(
    video_id: str,
    video_in: VideoUpdateExt, # 使用扩展模型
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    print(f"DEBUG: Update Video {video_id}")
    print(f"DEBUG: Payload {video_in.dict()}")

    video = session.get(Video, video_id)
    if not video or video.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Permission denied")

    # 更新常规字段
    video_data = video_in.dict(exclude_unset=True, exclude={"temp_thumbnail_path"})
    for key, value in video_data.items():
        setattr(video, key, value)

    # 处理封面转正
    if video_in.temp_thumbnail_path:
        print(f"DEBUG: Found temp thumb: {video_in.temp_thumbnail_path}")



        src_abs = video_in.temp_thumbnail_path.replace("/static", "/data/myvideo/static")
        print(f"DEBUG: Src Abs Path: {src_abs}, Exists: {os.path.exists(src_abs)}")

        new_filename = f"{video.id}_{int(time.time())}.jpg"
        dst_abs = f"/data/myvideo/static/thumbnails/{new_filename}"

        if os.path.exists(src_abs):
            shutil.move(src_abs, dst_abs)
            video.thumbnail_path = f"/static/thumbnails/{new_filename}"
            print(f"DEBUG: Moved to {dst_abs}, DB path updated.")
        else:
            print("DEBUG: Source file not found!")
    else:
        print("DEBUG: No temp thumbnail path provided.")

    session.add(video)
    session.commit()
    session.refresh(video)
    return video

@app.post("/videos/{video_id}/thumbnail/regenerate")
def regenerate_thumbnail(video_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    video = session.get(Video, video_id)
    if not video or video.user_id != current_user.id: raise HTTPException(status_code=404)

    # 生成到临时目录
    timestamp = f"00:00:{random.randint(5, 59):02d}"
    temp_filename = f"temp_{video.id}_{int(time.time())}.jpg"
    temp_abs_path = f"/data/myvideo/static/thumbnails/temp/{temp_filename}"

    subprocess.run(["ffmpeg", "-ss", timestamp, "-i", video.original_file_path, "-vframes", "1", temp_abs_path, "-y"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return {"url": f"/static/thumbnails/temp/{temp_filename}"}

@app.post("/videos/{video_id}/thumbnail/upload")
def upload_thumbnail(video_id: str, file: UploadFile = File(...), current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    video = session.get(Video, video_id)
    if not video or video.user_id != current_user.id: raise HTTPException(status_code=404)
    if not file.content_type.startswith("image/"): raise HTTPException(status_code=400)

    # 也是先存到临时目录
    temp_filename = f"temp_upload_{video.id}_{int(time.time())}.jpg"
    temp_abs_path = f"/data/myvideo/static/thumbnails/temp/{temp_filename}"

    with open(temp_abs_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    return {"url": f"/static/thumbnails/temp/{temp_filename}"}

# ... (其他接口保持不变: get_my_videos, delete_video, 社交模块, 认证模块)
# 为了节省篇幅，我假设其他接口已经有了。如果不确定，我会把它们补全。
@app.get("/users/me/videos", response_model=List[VideoRead])
def get_my_videos(current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    return session.exec(select(Video).where(Video.user_id == current_user.id).order_by(desc(Video.created_at))).all()

@app.delete("/videos/{video_id}")
def delete_video(video_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    video = session.get(Video, video_id)
    if not video or video.user_id != current_user.id: raise HTTPException(status_code=404)
    session.delete(video)
    session.commit()
    return {"ok": True}

# 社交和认证... (略，因为之前已经写过了，这里主要是为了覆盖 update_video)
# 警告：全量覆盖必须包含所有代码，否则会丢接口。我把所有代码合并一下。

@app.post("/users/{user_id}/follow")
def follow_user(user_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    if str(current_user.id) == user_id: raise HTTPException(status_code=400)
    if not session.exec(select(UserFollow).where(UserFollow.follower_id == current_user.id).where(UserFollow.followed_id == user_id)).first():
        session.add(UserFollow(follower_id=current_user.id, followed_id=user_id))
        session.commit()
    return {"ok": True}

@app.delete("/users/{user_id}/follow")
def unfollow_user(user_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    existing = session.exec(select(UserFollow).where(UserFollow.follower_id == current_user.id).where(UserFollow.followed_id == user_id)).first()
    if existing: session.delete(existing); session.commit()
    return {"ok": True}

@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: Annotated[OAuth2PasswordRequestForm, Depends()], session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == form_data.username)).first()
    if not user or not verify_password(form_data.password, user.hashed_password): raise HTTPException(status_code=401)
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/users/register", response_model=UserRead)
def register_user(user: UserCreate, session: Session = Depends(get_session)):
    if session.exec(select(User).where(User.username == user.username)).first(): raise HTTPException(status_code=400)
    new_user = User(username=user.username, email=user.email, hashed_password=get_password_hash(user.password))
    session.add(new_user); session.commit(); session.refresh(new_user)
    return new_user

@app.get("/users/me", response_model=UserRead)
async def read_users_me(token: Annotated[str, Depends(oauth2_scheme)], session: Session = Depends(get_session)):
    try: username = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]).get("sub")
    except JWTError: raise HTTPException(status_code=401)
    user = session.exec(select(User).where(User.username == username)).first()
    if not user: raise HTTPException(status_code=401)
    return user

@app.get("/")
def read_root(): return {"message": "MyVideo API is running!"}
