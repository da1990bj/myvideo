from typing import Annotated, List, Optional
from jose import JWTError, jwt
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form, Query, Body
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import Session, select, desc, update
from sqlalchemy import func
import shutil
import os
import random
import subprocess
import time
import asyncio
import logging
from uuid import uuid4, UUID
from datetime import datetime
from utils import clean_tags

# WebSocket support
import socketio
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

# --- Helpers ---
def create_notification(session: Session, sender_id: UUID, recipient_id: UUID, type: str, entity_id: str, content: str = None):
    if sender_id == recipient_id: return

    # Deduplicate for like/follow
    if type in ["follow", "like_video"]:
        existing = session.exec(select(Notification).where(
            Notification.recipient_id == recipient_id,
            Notification.sender_id == sender_id,
            Notification.type == type,
            Notification.entity_id == entity_id
        )).first()
        if existing: return

    notif = Notification(sender_id=sender_id, recipient_id=recipient_id, type=type, entity_id=entity_id, content=content)
    session.add(notif)
    # Don't commit here, let caller commit

from database import engine, get_session, init_db
from data_models import User, UserCreate, UserRead, UserLogin, Token, Video, VideoRead, Category, VideoUpdate, UserUpdate, UserFollow, UserBlock, UserPasswordUpdate, EmailUpdate, UserVideoHistory, Comment, VideoLike, Notification, VideoAuditLog, Tag, VideoTag, Collection, CollectionItem, CollectionCreate, CollectionRead, VideoFavorite, CollectionFavorite, Role, SystemConfig, AdminLog
from security import get_password_hash, verify_password, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES, SECRET_KEY, ALGORITHM
from tasks import transcode_video_task
from init_data import init_categories
from socketio_handler import manager
import psutil

app = FastAPI(title="MyVideo Backend", version="2.0.0")
app.mount("/static", StaticFiles(directory="/data/myvideo/static"), name="static")

# --- WebSocket 配置 (使用 python-socketio) ---
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins=['*'],
    cors_credentials=True,
    ping_timeout=60,
    ping_interval=25,
    engineio_logger=False,
    logger=False
)

# 将Socket.IO作为ASGI应用
socketio_app = socketio.ASGIApp(sio, app)

# 添加CORS支持
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    logger.info("MyVideo v2.0 startup complete - WebSocket support enabled")


# --- WebSocket 事件处理 ---

@sio.event
async def connect(sid, environ, auth):
    """
    WebSocket 连接事件处理
    客户端连接时自动触发

    auth 参数应包含有效的JWT token (通过auth dict传递)
    """
    try:
        # 提取并验证 JWT token
        token = None
        if auth and isinstance(auth, dict):
            token = auth.get('token')

        if not token:
            logger.warning(f"WebSocket connect attempt without token (SID: {sid})")
            raise ConnectionRefusedError('No token provided')

        # 验证 token 和获取用户
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username: str = payload.get("sub")
            if username is None:
                raise ConnectionRefusedError('Invalid token: no username')

            # 从数据库获取用户
            with Session(engine) as session:
                user = session.exec(select(User).where(User.username == username)).first()
                if not user:
                    raise ConnectionRefusedError('User not found')

                # 建立连接
                conn_info = await manager.connect(str(user.id), sid)
                logger.info(f"✅ WebSocket connected: User {user.username} ({user.id}), SID: {sid}")

        except JWTError as e:
            logger.warning(f"JWT validation failed in WebSocket connect (SID: {sid}): {str(e)}")
            raise ConnectionRefusedError(f'Invalid JWT token')

    except ConnectionRefusedError as e:
        logger.warning(f"WebSocket connection refused (SID: {sid}): {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in WebSocket connect handler (SID: {sid}): {str(e)}")
        raise ConnectionRefusedError(f'Connection error')


@sio.event
async def disconnect(sid):
    """
    WebSocket 断开连接事件处理
    """
    try:
        user_id = await manager.disconnect_by_sid(sid)
        if user_id:
            logger.info(f"User {user_id} disconnected from WebSocket (SID: {sid})")
    except Exception as e:
        logger.error(f"Error in WebSocket disconnect handler: {e}")


@sio.event
async def ping(sid):
    """
    心跳检测 - 客户端定期发送ping保持连接
    """
    return {"pong": True}


@sio.event
async def get_connection_info(sid):
    """
    获取当前连接池的统计信息（用于监控）
    """
    return manager.get_connection_info()

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

async def get_current_admin(current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user

# --- RBAC & Helpers ---

def log_admin_action(session: Session, admin_id: UUID, action: str, target_id: str = None, details: str = None, ip_address: str = None):
    log = AdminLog(admin_id=admin_id, action=action, target_id=target_id, details=details, ip_address=ip_address)
    session.add(log)
    # Note: Caller must commit

def check_permission(user: User, permission: str, session: Session) -> bool:
    if user.is_admin: # Legacy admin check fallback or superuser override
         # Ideally rely on Role, but for migration safety:
         # Check if role exists
         if user.role_id:
             role = session.get(Role, user.role_id)
             if role:
                 if role.permissions == "*": return True
                 perms = role.permissions.split(",")
                 return permission in perms
    # Default fail if no role or not admin
    return False

class PermissionChecker:
    def __init__(self, permission: str):
        self.permission = permission

    def __call__(self, user: User = Depends(get_current_user), session: Session = Depends(get_session)):
        if not user.is_active: raise HTTPException(status_code=400, detail="Inactive user")

        # Check Role
        if not user.role_id:
            raise HTTPException(status_code=403, detail="Role not assigned")

        role = session.get(Role, user.role_id)
        if not role:
             raise HTTPException(status_code=403, detail="Role not found")

        allowed = False
        if role.permissions == "*":
            allowed = True
        else:
            perms = role.permissions.split(",")
            if self.permission in perms:
                allowed = True

        if not allowed:
            raise HTTPException(status_code=403, detail=f"Permission '{self.permission}' required")

        return user


# --- Helper to process tags ---
def process_tags(session: Session, video: Video, tag_list: List[str]):
    # 1. Clear existing tags (simplest approach: remove associations)
    # For a new video, this is empty. For update, it clears.
    # Note: This doesn't decrease usage_count for simplicity in MVP, but ideally should.

    # Efficiently clear old relations
    session.exec(select(VideoTag).where(VideoTag.video_id == video.id))
    # Delete them
    current_tags = session.exec(select(VideoTag).where(VideoTag.video_id == video.id)).all()
    for tag_link in current_tags:
        session.delete(tag_link)
        # Decrease count
        tag = session.get(Tag, tag_link.tag_id)
        if tag:
            tag.usage_count = max(0, tag.usage_count - 1)
            session.add(tag)

    # 2. Add new tags
    for tag_name in tag_list:
        if not tag_name: continue
        tag_name = tag_name.strip()

        # Check if tag exists
        tag = session.exec(select(Tag).where(Tag.name == tag_name)).first()
        if not tag:
            tag = Tag(name=tag_name, usage_count=0)
            session.add(tag)
            session.commit() # Commit to get ID
            session.refresh(tag)

        # Create relation
        # Check if already added in this loop (though input should be deduped)
        # Since we cleared all, just add.
        # Check duplicates in input list

        link = VideoTag(video_id=video.id, tag_id=tag.id)
        session.add(link)

        tag.usage_count += 1
        session.add(tag)

# --- Public System Config API ---
@app.get("/system/config")
def get_public_system_config(session: Session = Depends(get_session)):
    """Get public system configuration (no auth required)"""
    configs = session.exec(select(SystemConfig)).all()
    result = {}
    # Only return public config like site_name
    public_keys = ["site_name", "site_notice"]
    for c in configs:
        if c.key in public_keys:
            result[c.key] = c.value
    return result

@app.post("/videos/upload", response_model=VideoRead)
async def upload_video(
    title: str = Form(...),
    description: str = Form(None),
    category_id: int = Form(None),
    tags: str = Form(""),
    file: UploadFile = File(...),
    current_user: User = Depends(PermissionChecker("video:upload")),
    session: Session = Depends(get_session)
):
    if not file.content_type.startswith("video/"): raise HTTPException(status_code=400, detail="File must be a video")
    file_id = uuid4()
    ext = os.path.splitext(file.filename)[1]
    save_filename = f"{file_id}{ext}"
    save_path = f"/data/myvideo/static/videos/uploads/{save_filename}"
    with open(save_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)

    # Clean Tags
    raw_tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    tag_list = clean_tags(raw_tag_list)

    # Create Video (without tags first)
    new_video = Video(id=file_id, title=title, description=description, category_id=category_id, user_id=current_user.id, original_file_path=save_path, status="pending")
    session.add(new_video)
    session.commit()
    session.refresh(new_video)

    # Process Tags
    if tag_list:
        process_tags(session, new_video, tag_list)
        session.commit()
        session.refresh(new_video)

    transcode_video_task.delay(str(new_video.id))
    return new_video

@app.get("/videos", response_model=List[VideoRead])
def get_videos(session: Session = Depends(get_session), page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), category_id: Optional[int] = Query(None), keyword: Optional[str] = Query(None), sort_by: str = Query("latest", enum=["latest", "popular"])):
    # Modify: Allow both 'completed' and 'approved'
    statement = select(Video).where(Video.status.in_(["completed", "approved"])).where(Video.visibility == "public")
    if category_id: statement = statement.where(Video.category_id == category_id)
    if keyword: statement = statement.where(Video.title.contains(keyword))
    if sort_by == "latest": statement = statement.order_by(desc(Video.created_at))
    elif sort_by == "popular": statement = statement.order_by(desc(Video.views))
    offset = (page - 1) * size
    return session.exec(statement.offset(offset).limit(size)).all()

@app.get("/videos/{video_id}", response_model=VideoRead)
def read_video(
    video_id: str,
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: Session = Depends(get_session)
):
    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404, detail="Video not found")

    # Convert to Read model first to avoid modifying ORM object
    video_read = VideoRead.from_orm(video)

    if current_user:
        # Check liked
        like = session.exec(select(VideoLike).where(VideoLike.user_id == current_user.id, VideoLike.video_id == video_id, VideoLike.like_type == 'like')).first()
        video_read.is_liked = like is not None

        # Check favorited
        fav = session.exec(select(VideoFavorite).where(VideoFavorite.user_id == current_user.id, VideoFavorite.video_id == video_id)).first()
        video_read.is_favorited = fav is not None

    # Check if video belongs to any collection (take the first one found)
    collection_item = session.exec(select(CollectionItem).where(CollectionItem.video_id == video_id)).first()
    if collection_item:
        video_read.collection_id = collection_item.collection_id

    return video_read

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
    statement = select(Video, UserVideoHistory).join(UserVideoHistory).where(UserVideoHistory.user_id == current_user.id).order_by(desc(UserVideoHistory.last_watched))
    offset = (page - 1) * size
    results = session.exec(statement.offset(offset).limit(size)).all()

    videos = []
    for video, history in results:
        # Convert to Read model and attach progress
        v_read = VideoRead.from_orm(video)
        v_read.progress = int(history.progress)
        videos.append(v_read)

    return videos

# --- Comments ---

@app.post("/videos/{video_id}/comments")
def create_comment(
    video_id: str,
    content: str = Form(...),
    parent_id: Optional[int] = Form(None),
    current_user: User = Depends(PermissionChecker("comment:create")),
    session: Session = Depends(get_session)
):
    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404)

    if parent_id:
        parent = session.get(Comment, parent_id)
        if not parent: raise HTTPException(status_code=404, detail="Parent comment not found")
        # Ensure parent comment belongs to the same video
        if str(parent.video_id) != video_id:
            raise HTTPException(status_code=400, detail="Parent comment must belong to the same video")

    comment = Comment(content=content, user_id=current_user.id, video_id=video_id, parent_id=parent_id)
    session.add(comment)
    session.flush() # Ensure ID is generated
    session.refresh(comment)

    # Notification logic
    if parent_id:
        # Reply: Notify parent comment author
        parent_comment = session.get(Comment, parent_id)
        if parent_comment:
            create_notification(session, current_user.id, parent_comment.user_id, "reply", f"{video_id}#{comment.id}", content)
    else:
        # Top-level comment: Notify video author
        create_notification(session, current_user.id, video.user_id, "comment", f"{video_id}#{comment.id}", content)

    session.commit()
    session.refresh(comment)
    return {
        "id": comment.id,
        "content": comment.content,
        "created_at": comment.created_at,
        "parent_id": comment.parent_id,
        "user": {"username": current_user.username, "avatar_path": current_user.avatar_path}
    }

@app.get("/videos/{video_id}/comments")
def get_comments(
    video_id: str,
    parent_id: Optional[int] = Query(None),
    session: Session = Depends(get_session),
    page: int = 1,
    size: int = 20
):
    statement = select(Comment).where(Comment.video_id == video_id)

    if parent_id:
        statement = statement.where(Comment.parent_id == parent_id)
    else:
        statement = statement.where(Comment.parent_id == None)

    statement = statement.order_by(desc(Comment.created_at))
    offset = (page - 1) * size
    comments = session.exec(statement.offset(offset).limit(size)).all()

    result = []
    for c in comments:
        user = session.get(User, c.user_id)

        # Handle deleted content display
        display_content = c.content
        if c.is_deleted:
            if c.deleted_by == "admin":
                display_content = "该评论已由系统删除"
            else:
                display_content = "该评论已由用户删除"

        # Get reply count
        reply_count = session.exec(select(func.count(Comment.id)).where(Comment.parent_id == c.id)).one()

        result.append({
            "id": c.id,
            "content": display_content,
            "created_at": c.created_at,
            "parent_id": c.parent_id,
            "reply_count": reply_count,
            "is_deleted": c.is_deleted,
            "user": {
                "username": user.username if user else "Unknown",
                "avatar_path": user.avatar_path if user else None
            }
        })
    return result

@app.get("/comments/{comment_id}")
def get_comment(
    comment_id: int,
    session: Session = Depends(get_session)
):
    comment = session.get(Comment, comment_id)
    if not comment: raise HTTPException(status_code=404)
    user = session.get(User, comment.user_id)
    return {
        "id": comment.id,
        "content": comment.content,
        "created_at": comment.created_at,
        "parent_id": comment.parent_id,
        "video_id": comment.video_id,
        "user": {
            "username": user.username if user else "Unknown",
            "avatar_path": user.avatar_path if user else None
        }
    }

@app.delete("/comments/{comment_id}")
def delete_comment(
    comment_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    comment = session.get(Comment, comment_id)
    if not comment: raise HTTPException(status_code=404)
    if comment.user_id != current_user.id: raise HTTPException(status_code=403)

    # Soft delete
    comment.is_deleted = True
    comment.deleted_by = "user"
    session.add(comment)
    session.commit()
    return {"ok": True}

# --- Admin APIs ---

@app.get("/admin/stats/system")
def get_system_stats(
    user: User = Depends(PermissionChecker("*")), # Super admin only
    session: Session = Depends(get_session)
):
    cpu_percent = psutil.cpu_percent(interval=None) # Non-blocking
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')

    return {
        "cpu_percent": cpu_percent,
        "memory": {
            "total": memory.total,
            "available": memory.available,
            "percent": memory.percent
        },
        "disk": {
            "total": disk.total,
            "free": disk.free,
            "percent": disk.percent
        }
    }

@app.get("/admin/roles")
def get_roles(
    user: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    return session.exec(select(Role)).all()

@app.post("/admin/roles")
def create_role(
    name: str = Body(..., embed=True),
    description: str = Body(None, embed=True),
    permissions: str = Body("", embed=True),
    user: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    if session.exec(select(Role).where(Role.name == name)).first():
        raise HTTPException(status_code=400, detail="Role already exists")

    role = Role(name=name, description=description, permissions=permissions)
    session.add(role)
    session.commit()
    session.refresh(role)
    log_admin_action(session, user.id, "create_role", str(role.id), name)
    return role

@app.put("/admin/roles/{role_id}")
def update_role(
    role_id: int,
    permissions: str = Body(..., embed=True),
    description: str = Body(None, embed=True),
    user: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    role = session.get(Role, role_id)
    if not role: raise HTTPException(status_code=404)
    role.permissions = permissions
    if description is not None:
        role.description = description
    session.add(role)
    session.commit()
    log_admin_action(session, user.id, "update_role", str(role_id), f"Permissions: {permissions}")
    return {"ok": True}

@app.get("/admin/config")
def get_system_config(
    user: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    configs = session.exec(select(SystemConfig)).all()
    result = {}
    for c in configs:
        result[c.key] = c.value
    return result

@app.put("/admin/config")
def update_system_config(
    config: dict = Body(...),
    user: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    for key, value in config.items():
        conf = session.get(SystemConfig, key)
        if conf:
            conf.value = str(value)
            conf.updated_at = datetime.utcnow()
            session.add(conf)
            log_admin_action(session, user.id, "update_config", key, f"Set to {value}")
    session.commit()
    return {"ok": True}

@app.get("/admin/logs")
def get_admin_logs(
    page: int = 1, size: int = 20,
    user: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    stmt = select(AdminLog).order_by(desc(AdminLog.created_at))
    logs = session.exec(stmt.offset((page-1)*size).limit(size)).all()

    # Enrich with admin username
    result = []
    for log in logs:
        admin_user = session.get(User, log.admin_id)
        result.append({
            "id": log.id,
            "admin_name": admin_user.username if admin_user else "Unknown",
            "action": log.action,
            "target_id": log.target_id,
            "details": log.details,
            "created_at": log.created_at
        })
    return result

@app.get("/admin/stats")
def get_admin_stats(
    admin: User = Depends(PermissionChecker("video:audit")), # Allow auditors too
    session: Session = Depends(get_session)
):
    users = session.exec(select(func.count()).select_from(User)).one()
    videos = session.exec(select(func.count()).select_from(Video)).one()
    comments = session.exec(select(func.count()).select_from(Comment)).one()
    return {"users": users, "videos": videos, "comments": comments}

@app.get("/admin/users", response_model=List[UserRead])
def get_all_users(
    page: int = 1, size: int = 20,
    admin: User = Depends(PermissionChecker("user:ban")), # Require user management perm
    session: Session = Depends(get_session)
):
    users = session.exec(select(User).order_by(desc(User.created_at)).offset((page-1)*size).limit(size)).all()
    result = []
    for u in users:
        u_read = UserRead.from_orm(u)
        if u.role:
            u_read.role_name = u.role.name
        else:
            u_read.role_name = "无角色"
        result.append(u_read)
    return result

@app.post("/admin/users/{user_id}/status")
def update_user_status(
    user_id: str,
    is_active: bool,
    admin: User = Depends(PermissionChecker("user:ban")),
    session: Session = Depends(get_session)
):
    user = session.get(User, user_id)
    if not user: raise HTTPException(status_code=404)
    user.is_active = is_active
    session.add(user)

    log_admin_action(session, admin.id, "update_user_status", user_id, f"Set active={is_active}")

    session.commit()
    return {"ok": True}

@app.put("/admin/users/{user_id}/role")
def update_user_role(
    user_id: str,
    role_id: int = Body(..., embed=True),
    admin: User = Depends(PermissionChecker("admin:super")), # Only super admin can change roles
    session: Session = Depends(get_session)
):
    user = session.get(User, user_id)
    if not user: raise HTTPException(status_code=404)

    role = session.get(Role, role_id)
    if not role: raise HTTPException(status_code=404, detail="Role not found")

    user.role_id = role.id

    # Backward compatibility
    if role.name == "Super Admin":
        user.is_admin = True
    else:
        user.is_admin = False

    session.add(user)
    log_admin_action(session, admin.id, "update_user_role", user_id, f"Role set to {role.name}")

    session.commit()
    return {"ok": True}

@app.get("/admin/videos", response_model=List[VideoRead])
def get_all_videos(
    page: int = 1, size: int = 20,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    return session.exec(select(Video).order_by(desc(Video.created_at)).offset((page-1)*size).limit(size)).all()

@app.post("/admin/videos/{video_id}/ban")
def ban_video(
    video_id: str,
    reason: str = Body(..., embed=True),
    admin: User = Depends(PermissionChecker("video:ban")),
    session: Session = Depends(get_session)
):
    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404)

    video.status = "banned"
    video.ban_reason = reason
    session.add(video)

    log = VideoAuditLog(video_id=video_id, operator_id=admin.id, action="ban", reason=reason)
    session.add(log)

    log_admin_action(session, admin.id, "ban_video", video_id, reason)

    create_notification(session, admin.id, video.user_id, "system", str(video.id), f"您的视频《{video.title}》因违规被下架: {reason}")

    session.commit()
    return {"ok": True}

@app.post("/admin/videos/{video_id}/approve")
def approve_video(
    video_id: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404)

    video.status = "approved" # Changed from completed to approved
    video.ban_reason = None
    session.add(video)

    log = VideoAuditLog(video_id=video_id, operator_id=admin.id, action="approve")
    session.add(log)

    log_admin_action(session, admin.id, "approve_video", video_id)

    create_notification(session, admin.id, video.user_id, "system", str(video.id), f"您的视频《{video.title}》申诉/审核已通过并上架")

    session.commit()
    return {"ok": True}

@app.post("/videos/{video_id}/appeal")
def appeal_video(
    video_id: str,
    reason: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404)
    if video.user_id != current_user.id: raise HTTPException(status_code=403)
    if video.status != "banned": raise HTTPException(status_code=400, detail="Only banned videos can be appealed")

    video.status = "appealing"
    session.add(video)

    log = VideoAuditLog(video_id=video_id, operator_id=current_user.id, action="appeal", reason=reason)
    session.add(log)

    session.commit()
    return {"ok": True}

@app.get("/videos/{video_id}/audit-logs")
def get_audit_logs(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404)

    # Allow if admin (with permission) or owner
    is_admin = False
    if current_user.role_id:
        role = session.get(Role, current_user.role_id)
        if role and (role.permissions == "*" or "video:audit" in role.permissions):
            is_admin = True

    if not is_admin and video.user_id != current_user.id:
        raise HTTPException(status_code=403)

    logs = session.exec(select(VideoAuditLog).where(VideoAuditLog.video_id == video_id).order_by(desc(VideoAuditLog.created_at))).all()

    result = []
    for l in logs:
        op = session.get(User, l.operator_id)
        result.append({
            "action": l.action,
            "reason": l.reason,
            "created_at": l.created_at,
            "operator_name": op.username if op else "Unknown",
            "is_admin": op.is_admin if op else False
        })
    return result

@app.post("/admin/videos/{video_id}/status")
def update_video_status(
    video_id: str,
    status: str,
    visibility: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404)
    video.status = status
    video.visibility = visibility
    session.add(video)

    log_admin_action(session, admin.id, "update_video_status", video_id, f"Status: {status}, Visibility: {visibility}")

    session.commit()
    return {"ok": True}

@app.get("/admin/comments")
def get_all_comments(
    page: int = 1, size: int = 20,
    admin: User = Depends(PermissionChecker("comment:delete")), # Usually auditors view comments
    session: Session = Depends(get_session)
):
    comments = session.exec(select(Comment).order_by(desc(Comment.created_at)).offset((page-1)*size).limit(size)).all()
    result = []
    for c in comments:
        user = session.get(User, c.user_id)
        result.append({
            "id": c.id,
            "content": c.content,
            "created_at": c.created_at,
            "user": {"username": user.username if user else "Unknown"},
            "video_id": c.video_id
        })
    return result

@app.delete("/admin/comments/{comment_id}")
def delete_any_comment(
    comment_id: int,
    admin: User = Depends(PermissionChecker("comment:delete")),
    session: Session = Depends(get_session)
):
    comment = session.get(Comment, comment_id)
    if comment:
        comment.is_deleted = True
        comment.deleted_by = "admin"
        session.add(comment)

        log_admin_action(session, admin.id, "delete_comment", str(comment_id))

        session.commit()
    return {"ok": True}

# --- Notification APIs ---

@app.get("/notifications")
def get_notifications(
    page: int = 1, size: int = 20,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    stmt = select(Notification).where(Notification.recipient_id == current_user.id).order_by(desc(Notification.created_at))
    offset = (page - 1) * size
    notifs = session.exec(stmt.offset(offset).limit(size)).all()

    result = []
    for n in notifs:
        sender = session.get(User, n.sender_id)
        result.append({
            "id": n.id,
            "type": n.type,
            "entity_id": n.entity_id,
            "content": n.content,
            "is_read": n.is_read,
            "created_at": n.created_at,
            "sender": {
                "id": sender.id if sender else None,
                "username": sender.username if sender else "Unknown",
                "avatar_path": sender.avatar_path if sender else None
            }
        })
    return result

@app.get("/notifications/unread-count")
def get_unread_count(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    # Count manually to avoid importing func.count if not needed, or verify import
    # Assuming list len is fast enough for MVP
    count = len(session.exec(select(Notification).where(Notification.recipient_id == current_user.id, Notification.is_read == False)).all())
    return {"count": count}

@app.post("/notifications/read-all")
def mark_all_read(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    notifs = session.exec(select(Notification).where(Notification.recipient_id == current_user.id, Notification.is_read == False)).all()
    for n in notifs: n.is_read = True
    session.add_all(notifs)
    session.commit()
    return {"ok": True}

@app.post("/notifications/{notif_id}/read")
def mark_one_read(
    notif_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    notif = session.get(Notification, notif_id)
    if notif and notif.recipient_id == current_user.id:
        notif.is_read = True
        session.add(notif)
        session.commit()
    return {"ok": True}

@app.post("/videos/{video_id}/like")
async def like_video(
    video_id: UUID,
    like_type: str, # "like" or "dislike"
    current_user: User = Depends(PermissionChecker("social:interaction")),
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
            if like_type == 'like':
                video.like_count = max(0, video.like_count - 1)
                session.add(video)
            session.commit()
            return {"status": "unliked", "like_type": like_type, "like_count": video.like_count}
        else:
            # 改变点赞/踩类型
            # 如果是从 dislike 变 like，like_count + 1
            # 如果是从 like 变 dislike，like_count - 1
            if existing_like.like_type == 'dislike' and like_type == 'like':
                video.like_count += 1
            elif existing_like.like_type == 'like' and like_type == 'dislike':
                video.like_count = max(0, video.like_count - 1)

            existing_like.like_type = like_type
            session.add(existing_like)
            session.add(video)
            session.commit()
            session.refresh(existing_like)
            return {"status": "changed", "like_type": like_type, "like_count": video.like_count}
    else:
        # 新增点赞或踩
        new_like = VideoLike(user_id=current_user.id, video_id=video_id, like_type=like_type)
        session.add(new_like)
        if like_type == "like":
            video.like_count += 1
            session.add(video)
            create_notification(session, current_user.id, video.user_id, "like_video", str(video_id), f"赞了你的视频: {video.title}")
        session.commit()
        session.refresh(new_like)
        return {"status": "liked", "like_type": like_type, "like_count": video.like_count}

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
        if existing_like.like_type == 'like':
            video.like_count = max(0, video.like_count - 1)
            session.add(video)
        session.commit()
        return {"status": "unliked", "like_type": existing_like.like_type, "like_count": video.like_count}
    else:
        raise HTTPException(status_code=404, detail="Not liked or disliked by user")

@app.get("/categories", response_model=List[Category])
def get_categories(session: Session = Depends(get_session)):
    return session.exec(select(Category)).all()

# --- Favorites ---

@app.post("/videos/{video_id}/favorite")
def favorite_video(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404)

    existing = session.exec(select(VideoFavorite).where(VideoFavorite.user_id == current_user.id, VideoFavorite.video_id == video_id)).first()
    if existing:
        # Cancel favorite
        session.delete(existing)
        video.favorite_count = max(0, video.favorite_count - 1)
        status = "unfavorited"
    else:
        # Add favorite
        fav = VideoFavorite(user_id=current_user.id, video_id=video_id)
        session.add(fav)
        video.favorite_count += 1
        status = "favorited"

    session.add(video)
    session.commit()
    return {"status": status, "favorite_count": video.favorite_count}

@app.post("/collections/{collection_id}/favorite")
def favorite_collection(
    collection_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    col = session.get(Collection, collection_id)
    if not col: raise HTTPException(status_code=404)

    existing = session.exec(select(CollectionFavorite).where(CollectionFavorite.user_id == current_user.id, CollectionFavorite.collection_id == collection_id)).first()
    if existing:
        session.delete(existing)
        col.favorite_count = max(0, col.favorite_count - 1)
        status = "unfavorited"
    else:
        fav = CollectionFavorite(user_id=current_user.id, collection_id=collection_id)
        session.add(fav)
        col.favorite_count += 1
        status = "favorited"

    session.add(col)
    session.commit()
    return {"status": status, "favorite_count": col.favorite_count}

@app.get("/users/me/favorites/videos", response_model=List[VideoRead])
def get_my_favorite_videos(
    page: int = 1, size: int = 20,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    stmt = select(Video).join(VideoFavorite).where(VideoFavorite.user_id == current_user.id).order_by(desc(VideoFavorite.created_at))
    return session.exec(stmt.offset((page-1)*size).limit(size)).all()

@app.get("/users/me/favorites/collections", response_model=List[CollectionRead])
def get_my_favorite_collections(
    page: int = 1, size: int = 20,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    stmt = select(Collection).join(CollectionFavorite).where(CollectionFavorite.user_id == current_user.id).order_by(desc(CollectionFavorite.created_at))
    collections = session.exec(stmt.offset((page-1)*size).limit(size)).all()

    # Reuse enrichment logic
    result = []
    for c in collections:
        video_count = session.exec(select(func.count()).select_from(CollectionItem).where(CollectionItem.collection_id == c.id)).one()
        c_read = CollectionRead.from_orm(c)
        c_read.video_count = video_count
        c_read.owner = session.get(User, c.user_id)
        c_read.is_favorited = True # Since we queried favorites table

        first_item = session.exec(select(CollectionItem).where(CollectionItem.collection_id == c.id).order_by(CollectionItem.order).limit(1)).first()
        if first_item:
            c_read.first_video_id = first_item.video_id
            if not c_read.cover_image:
                first_video = session.get(Video, first_item.video_id)
                if first_video and first_video.thumbnail_path:
                    c_read.cover_image = first_video.thumbnail_path
        result.append(c_read)
    return result

@app.get("/users/me/liked/videos", response_model=List[VideoRead])
def get_my_liked_videos(
    page: int = 1, size: int = 20,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    stmt = select(Video).join(VideoLike).where(VideoLike.user_id == current_user.id, VideoLike.like_type == 'like').order_by(desc(VideoLike.created_at))
    return session.exec(stmt.offset((page-1)*size).limit(size)).all()

# --- Collections ---

@app.post("/collections", response_model=CollectionRead)
def create_collection(
    collection: CollectionCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    new_collection = Collection(
        title=collection.title,
        description=collection.description,
        user_id=current_user.id
    )
    session.add(new_collection)
    session.commit()
    session.refresh(new_collection)
    return new_collection

@app.get("/collections", response_model=List[CollectionRead])
def get_collections(
    user_id: Optional[str] = Query(None),
    page: int = 1, size: int = 20,
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: Session = Depends(get_session)
):
    stmt = select(Collection)
    if user_id:
        target_user = get_user_by_id_or_username(user_id, session)
        if target_user:
            stmt = stmt.where(Collection.user_id == target_user.id)

    stmt = stmt.order_by(desc(Collection.created_at))
    collections = session.exec(stmt.offset((page-1)*size).limit(size)).all()

    # Enrich with video count and owner
    result = []
    for c in collections:
        video_count = session.exec(select(func.count()).select_from(CollectionItem).where(CollectionItem.collection_id == c.id)).one()
        c_read = CollectionRead.from_orm(c)
        c_read.video_count = video_count
        c_read.owner = session.get(User, c.user_id)

        if current_user:
            fav = session.exec(select(CollectionFavorite).where(CollectionFavorite.user_id == current_user.id, CollectionFavorite.collection_id == c.id)).first()
            c_read.is_favorited = fav is not None

        # Get first video ID
        first_item = session.exec(select(CollectionItem).where(CollectionItem.collection_id == c.id).order_by(CollectionItem.order).limit(1)).first()
        if first_item:
            c_read.first_video_id = first_item.video_id
            # Set default cover if not set
            if not c_read.cover_image:
                first_video = session.get(Video, first_item.video_id)
                if first_video and first_video.thumbnail_path:
                    c_read.cover_image = first_video.thumbnail_path

        result.append(c_read)
    return result

@app.get("/collections/{collection_id}")
def get_collection_details(
    collection_id: UUID,
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: Session = Depends(get_session)
):
    collection = session.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    # Get videos in order
    stmt = select(Video, CollectionItem.order).join(CollectionItem).where(CollectionItem.collection_id == collection_id).order_by(CollectionItem.order)
    results = session.exec(stmt).all()

    videos = []
    for vid, order in results:
        v_read = VideoRead.from_orm(vid)
        # We can attach order if needed, but VideoRead doesn't have it.
        videos.append(v_read)

    c_read = CollectionRead.from_orm(collection)
    c_read.video_count = len(videos)
    c_read.owner = session.get(User, collection.user_id)

    if current_user:
        fav = session.exec(select(CollectionFavorite).where(CollectionFavorite.user_id == current_user.id, CollectionFavorite.collection_id == collection_id)).first()
        c_read.is_favorited = fav is not None

    return {
        "collection": c_read,
        "videos": videos
    }

@app.post("/collections/{collection_id}/videos")
def add_video_to_collection(
    collection_id: UUID,
    video_id: UUID = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    collection = session.get(Collection, collection_id)
    if not collection: raise HTTPException(status_code=404)
    if collection.user_id != current_user.id: raise HTTPException(status_code=403)

    video = session.get(Video, video_id)
    if not video: raise HTTPException(status_code=404, detail="Video not found")

    # Check if already exists
    existing = session.exec(select(CollectionItem).where(CollectionItem.collection_id == collection_id, CollectionItem.video_id == video_id)).first()
    if existing: return {"ok": True, "message": "Already in collection"}

    # Get max order
    max_order = session.exec(select(func.max(CollectionItem.order)).where(CollectionItem.collection_id == collection_id)).one()
    new_order = (max_order if max_order is not None else -1) + 1

    item = CollectionItem(collection_id=collection_id, video_id=video_id, order=new_order)
    session.add(item)
    session.commit()
    return {"ok": True}

@app.delete("/collections/{collection_id}/videos/{video_id}")
def remove_video_from_collection(
    collection_id: UUID,
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    collection = session.get(Collection, collection_id)
    if not collection: raise HTTPException(status_code=404)
    if collection.user_id != current_user.id: raise HTTPException(status_code=403)

    item = session.exec(select(CollectionItem).where(CollectionItem.collection_id == collection_id, CollectionItem.video_id == video_id)).first()
    if item:
        session.delete(item)
        session.commit()
    return {"ok": True}

@app.put("/collections/{collection_id}/reorder")
def reorder_collection_videos(
    collection_id: UUID,
    video_ids: List[UUID] = Body(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    collection = session.get(Collection, collection_id)
    if not collection: raise HTTPException(status_code=404)
    if collection.user_id != current_user.id: raise HTTPException(status_code=403)

    # Verify all videos belong to this collection (optional but good for integrity)
    # Ideally we just update the order for the ones present.

    for index, vid in enumerate(video_ids):
        item = session.exec(select(CollectionItem).where(CollectionItem.collection_id == collection_id, CollectionItem.video_id == vid)).first()
        if item:
            item.order = index
            session.add(item)

    session.commit()
    return {"ok": True}

@app.delete("/collections/{collection_id}")
def delete_collection(
    collection_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    collection = session.get(Collection, collection_id)
    if not collection: raise HTTPException(status_code=404)
    if collection.user_id != current_user.id: raise HTTPException(status_code=403)

    items = session.exec(select(CollectionItem).where(CollectionItem.collection_id == collection_id)).all()
    for item in items:
        session.delete(item)

    session.delete(collection)
    session.commit()
    return {"ok": True}


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
    video_data = video_in.dict(exclude_unset=True, exclude={"temp_thumbnail_path", "tags"})

    # 清洗标签
    if video_in.tags is not None:
        tag_list = clean_tags(video_in.tags)
        process_tags(session, video, tag_list)

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
def follow_user(user_id: str, current_user: User = Depends(PermissionChecker("social:interaction")), session: Session = Depends(get_session)):
    target_user = get_user_by_id_or_username(user_id, session)
    if not target_user: raise HTTPException(status_code=404, detail="User not found")
    real_user_id = target_user.id

    if current_user.id == real_user_id: raise HTTPException(status_code=400, detail="Cannot follow yourself")

    if not session.exec(select(UserFollow).where(UserFollow.follower_id == current_user.id).where(UserFollow.followed_id == real_user_id)).first():
        session.add(UserFollow(follower_id=current_user.id, followed_id=real_user_id))
        create_notification(session, current_user.id, real_user_id, "follow", str(real_user_id), "关注了你")
        session.commit()
    return {"ok": True}

@app.delete("/users/{user_id}/follow")
def unfollow_user(user_id: str, current_user: User = Depends(PermissionChecker("social:interaction")), session: Session = Depends(get_session)):
    target_user = get_user_by_id_or_username(user_id, session)
    if not target_user: raise HTTPException(status_code=404, detail="User not found")
    real_user_id = target_user.id

    existing = session.exec(select(UserFollow).where(UserFollow.follower_id == current_user.id).where(UserFollow.followed_id == real_user_id)).first()
    if existing: session.delete(existing); session.commit()
    return {"ok": True}

@app.get("/users/me/following", response_model=List[UserRead])
def get_my_following(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    # 使用 join 提高效率，直接获取 User 对象
    stmt = select(User).join(UserFollow, User.id == UserFollow.followed_id).where(UserFollow.follower_id == current_user.id)
    return session.exec(stmt).all()

@app.get("/users/me/blocks", response_model=List[UserRead])
def get_my_blocks(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    stmt = select(User).join(UserBlock, User.id == UserBlock.blocked_id).where(UserBlock.blocker_id == current_user.id)
    return session.exec(stmt).all()

@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: Annotated[OAuth2PasswordRequestForm, Depends()], session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == form_data.username)).first()
    if not user or not verify_password(form_data.password, user.hashed_password): raise HTTPException(status_code=401)
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/users/register", response_model=UserRead)
def register_user(user: UserCreate, session: Session = Depends(get_session)):
    if session.exec(select(User).where(User.username == user.username)).first(): raise HTTPException(status_code=400)

    # Assign default role
    default_role = session.exec(select(Role).where(Role.name == "Standard User")).first()
    role_id = default_role.id if default_role else None

    new_user = User(username=user.username, email=user.email, hashed_password=get_password_hash(user.password), role_id=role_id)
    session.add(new_user); session.commit(); session.refresh(new_user)
    return new_user

@app.get("/users/me", response_model=UserRead)
async def read_users_me(token: Annotated[str, Depends(oauth2_scheme)], session: Session = Depends(get_session)):
    try: username = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]).get("sub")
    except JWTError: raise HTTPException(status_code=401)
    user = session.exec(select(User).where(User.username == username)).first()
    if not user: raise HTTPException(status_code=401)
    return user

def get_user_by_id_or_username(identifier: str, session: Session) -> Optional[User]:
    # Try UUID
    try:
        user_uuid = UUID(identifier)
        user = session.get(User, user_uuid)
        if user: return user
    except ValueError:
        pass

    # Try Username
    user = session.exec(select(User).where(User.username == identifier)).first()
    return user

@app.get("/users/{user_id}/profile")
def get_user_profile(
    user_id: str,
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: Session = Depends(get_session)
):
    target_user = get_user_by_id_or_username(user_id, session)
    if not target_user: raise HTTPException(status_code=404, detail="User not found")

    # Use real ID for queries
    real_user_id = target_user.id

    # Followers count
    followers_count = session.exec(select(func.count()).select_from(UserFollow).where(UserFollow.followed_id == real_user_id)).one()
    following_count = session.exec(select(func.count()).select_from(UserFollow).where(UserFollow.follower_id == real_user_id)).one()

    is_following = False
    if current_user and current_user.id != real_user_id:
        is_following = session.exec(select(UserFollow).where(UserFollow.follower_id == current_user.id, UserFollow.followed_id == real_user_id)).first() is not None

    videos_count = session.exec(select(func.count()).select_from(Video).where(Video.user_id == real_user_id, Video.status.in_(["completed", "approved"]), Video.visibility == "public")).one()

    is_self = False
    if current_user and current_user.id == real_user_id:
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
    target_user = get_user_by_id_or_username(user_id, session)
    if not target_user: raise HTTPException(status_code=404, detail="User not found")

    return session.exec(select(Video).where(Video.user_id == target_user.id, Video.status.in_(["completed", "approved"]), Video.visibility == "public").order_by(desc(Video.created_at))).all()

@app.get("/users/me/stats")
def get_my_creator_stats(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    # Total Likes (sum of like_count of my videos)
    total_likes = session.exec(select(func.sum(Video.like_count)).where(Video.user_id == current_user.id)).one() or 0
    # Total Favorites (sum of favorite_count of my videos)
    total_video_favs = session.exec(select(func.sum(Video.favorite_count)).where(Video.user_id == current_user.id)).one() or 0
    # Total Collection Favs (sum of favorite_count of my collections)
    total_col_favs = session.exec(select(func.sum(Collection.favorite_count)).where(Collection.user_id == current_user.id)).one() or 0
    # Total Views
    total_views = session.exec(select(func.sum(Video.views)).where(Video.user_id == current_user.id)).one() or 0

    return {
        "total_likes": total_likes,
        "total_favorites": total_video_favs + total_col_favs,
        "total_views": total_views
    }

@app.get("/")
def read_root(): return {"message": "MyVideo API is running!"}
