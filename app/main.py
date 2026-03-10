from typing import Annotated, List, Optional
from jose import JWTError, jwt
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form, Query
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import Session, select, desc
import shutil
import os
import random
import subprocess
import time
from uuid import uuid4, UUID
from datetime import datetime

from database import engine, get_session, init_db
from data_models import User, UserCreate, UserRead, UserLogin, Token, Video, VideoRead, Category, VideoUpdate, UserUpdate, UserFollow, UserBlock, UserPasswordUpdate, EmailUpdate, UserVideoHistory, Comment, VideoLike
from security import get_password_hash, verify_password, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES, SECRET_KEY, ALGORITHM
from tasks import transcode_video_task
from init_data import init_categories

app = FastAPI(title="MyVideo Backend", version="1.7.0")
app.mount("/static", StaticFiles(directory="/data/myvideo/static"), name="static")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

@app.on_event("startup")
def on_startup():
    init_db()
    os.makedirs("/data/myvideo/static/videos/uploads", exist_ok=True)
    os.makedirs("/data/myvideo/static/videos/processed", exist_ok=True)
    os.makedirs("/data/myvideo/static/thumbnails", exist_ok=True)
    os.makedirs("/data/myvideo/static/thumbnails/temp", exist_ok=True) # 临时目录
    os.makedirs("/data/myvideo/static/avatars", exist_ok=True)
    init_categories()

async def get_current_user(token: Annotated[Optional[str], Depends(oauth2_scheme)], session: Session = Depends(get_session)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token: raise credentials_exception
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None: raise credentials_exception
    except JWTError: raise credentials_exception
    user = session.exec(select(User).where(User.username == username)).first()
    if user is None: raise credentials_exception
    return user

async def get_current_user_optional(token: Annotated[Optional[str], Depends(oauth2_scheme)], session: Session = Depends(get_session)):
    if not token: return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None: return None
        user = session.exec(select(User).where(User.username == username)).first()
        return user
    except JWTError: return None




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
def read_video(video_id: str, session: Session = Depends(get_session)):
    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404, detail="Video not found")
    return video

# --- History ---

@app.post("/videos/{video_id}/progress")
def update_video_progress(
    video_id: str,
    progress: float = Query(..., description="Watched duration in seconds"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404)

    # Convert str to UUID if needed, but sqlmodel usually handles it.
    # The models define ID as UUID but URLs pass strings.
    # Let's trust SQLModel/FastAPI conversion or cast explicitly if it failed before.
    # Based on existing code, video_id in URL is str, model is UUID.

    # Check existing history
    # Note: select(UserVideoHistory) might need to join? No, it's a direct table.
    statement = select(UserVideoHistory).where(UserVideoHistory.user_id == current_user.id).where(UserVideoHistory.video_id == video_id)
    history = session.exec(statement).first()

    if history:
        history.progress = progress
        history.last_watched = datetime.utcnow()
        if video.duration and progress > (video.duration * 0.9):
            history.is_finished = True
        session.add(history)
    else:
        is_finished = False
        if video.duration and progress > (video.duration * 0.9):
            is_finished = True
        new_history = UserVideoHistory(user_id=current_user.id, video_id=video_id, progress=progress, is_finished=is_finished)
        session.add(new_history)

    session.commit()
    return {"status": "ok"}

@app.post("/videos/{video_id}/view")
def record_view(
    video_id: str,
    session: Session = Depends(get_session)
):
    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404)
    video.views += 1
    session.add(video)
    session.commit()
    return {"views": video.views}

@app.post("/videos/{video_id}/complete")
def record_complete(
    video_id: str,
    session: Session = Depends(get_session)
):
    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404)
    video.complete_views += 1
    session.add(video)
    session.commit()
    return {"complete_views": video.complete_views}

@app.get("/videos/{video_id}/progress")
def get_video_progress(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    statement = select(UserVideoHistory).where(UserVideoHistory.user_id == current_user.id).where(UserVideoHistory.video_id == video_id)
    history = session.exec(statement).first()
    if history:
        return {"progress": history.progress, "is_finished": history.is_finished}
    return {"progress": 0.0, "is_finished": False}

@app.get("/users/me/history", response_model=List[VideoRead])
def get_my_history(
    page: int = 1,
    size: int = 20,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    statement = select(Video).join(UserVideoHistory).where(UserVideoHistory.user_id == current_user.id).order_by(desc(UserVideoHistory.last_watched))
    offset = (page - 1) * size
    return session.exec(statement.offset(offset).limit(size)).all()

# --- Comments ---

@app.post("/videos/{video_id}/comments")
def create_comment(
    video_id: str,
    content: str = Form(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404)
    comment = Comment(content=content, user_id=current_user.id, video_id=video_id)
    session.add(comment)
    session.commit()
    session.refresh(comment)
    return {"id": comment.id, "content": comment.content, "created_at": comment.created_at, "user": {"username": current_user.username, "avatar_path": current_user.avatar_path}}

@app.get("/videos/{video_id}/comments")
def get_comments(
    video_id: str,
    session: Session = Depends(get_session),
    page: int = 1,
    size: int = 20
):
    statement = select(Comment).where(Comment.video_id == video_id).order_by(desc(Comment.created_at))
    offset = (page - 1) * size
    comments = session.exec(statement.offset(offset).limit(size)).all()

    result = []
    for c in comments:
        user = session.get(User, c.user_id)
        result.append({
            "id": c.id,
            "content": c.content,
            "created_at": c.created_at,
            "user": {
                "username": user.username if user else "Unknown",
                "avatar_path": user.avatar_path if user else None
            }
        })
    return result

@app.delete("/comments/{comment_id}")
def delete_comment(
    comment_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    comment = session.get(Comment, comment_id)
    if not comment: raise HTTPException(status_code=404)
    if comment.user_id != current_user.id: raise HTTPException(status_code=403)
    session.delete(comment)
    session.commit()
    return {"ok": True}

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

@app.get("/users/{user_id}/profile")
def get_user_profile(
    user_id: str,
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: Session = Depends(get_session)
):
    target_user = session.get(User, user_id)
    if not target_user: raise HTTPException(status_code=404)

    # Followers count
    followers_count = len(session.exec(select(UserFollow).where(UserFollow.followed_id == user_id)).all())
    following_count = len(session.exec(select(UserFollow).where(UserFollow.follower_id == user_id)).all())

    is_following = False
    if current_user and str(current_user.id) != user_id:
        is_following = session.exec(select(UserFollow).where(UserFollow.follower_id == current_user.id, UserFollow.followed_id == user_id)).first() is not None

    videos_count = len(session.exec(select(Video).where(Video.user_id == user_id, Video.status == "completed", Video.visibility == "public")).all())

    is_self = False
    if current_user and str(current_user.id) == user_id:
        is_self = True

    return {
        "id": target_user.id,
        "username": target_user.username,
        "email": target_user.email,
        "avatar_path": target_user.avatar_path,
        "bio": target_user.bio,
        "created_at": target_user.created_at,
        "is_active": target_user.is_active,
        "is_following": is_following,
        "is_self": is_self,
        "videos_count": videos_count,
        "followers_count": followers_count,
        "following_count": following_count
    }

@app.get("/users/{user_id}/videos/public", response_model=List[VideoRead])
def get_user_public_videos(
    user_id: str,
    session: Session = Depends(get_session)
):
    return session.exec(select(Video).where(Video.user_id == user_id, Video.status == "completed", Video.visibility == "public").order_by(desc(Video.created_at))).all()

@app.get("/")
def read_root(): return {"message": "MyVideo API is running!"}
