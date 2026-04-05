"""
视频相关路由
"""
from typing import List, Optional
from uuid import uuid4, UUID
from pathlib import Path
from datetime import datetime, timedelta
import io
import json
import os
import shutil
import secrets

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Body, Query, status, Request
from sqlmodel import Session, select
from sqlalchemy import and_

from database import get_session
from data_models import (
    Video, VideoRead, VideoUpdate, VideoLike, VideoFavorite,
    Comment, Category, User, Role, UserRole, VideoAuditLog, CollectionItem,
    TranscodeTask, UploadSession, UploadSessionRead, Notification
)
from dependencies import get_current_user, get_current_user_optional, PermissionChecker, process_tags
from tasks import transcode_video_task
from config import settings, get_transcode_config
import socketio_handler

router = APIRouter(prefix="", tags=["视频"])


@router.post("/videos/upload", response_model=VideoRead)
async def upload_video(
    title: str = Form(...),
    description: str = Form(""),
    category_id: int = Form(None),
    tags: str = Form(""),
    file: UploadFile = File(...),
    current_user: User = Depends(PermissionChecker("video:upload")),
    session: Session = Depends(get_session)
):
    """
    上传新视频
    """
    # 支持的视频格式
    ALLOWED_VIDEO_TYPES = {
        "video/mp4", "video/mpeg", "video/quicktime", "video/x-msvideo",
        "video/x-ms-wmv", "video/webm", "video/x-matroska", "video/matroska",
        "video/3gpp", "video/x-flv", "video/x-m4v", "video/ogg",
        "application/octet-stream"
    }
    ALLOWED_EXTENSIONS = {".mp4", ".mpeg", ".mpg", ".mov", ".avi", ".wmv", ".webm", ".mkv", ".3gp", ".flv", ".m4v", ".ogv"}

    # 检查文件格式
    ext = os.path.splitext(file.filename)[1].lower() if file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的视频格式: {ext}。支持的格式: MP4, MOV, AVI, WMV, WebM, MKV, MPEG, 3GP, FLV")

    # 规范化 content_type（去除参数如charset）
    content_type = file.content_type.split(";")[0].strip().lower()
    # 如果扩展名合法，则更宽松地接受 content_type（某些浏览器对MKV等格式的MIME类型判断不准确）
    if content_type not in ALLOWED_VIDEO_TYPES:
        # 允许 video/* 或 application/octet-stream，只要扩展名合法
        if not (content_type.startswith("video/") or content_type == "application/octet-stream") or ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"文件类型不被支持: {content_type}")

    # 检查上传大小限制
    # 读取文件内容到内存检查大小
    file_content = await file.read()
    file_size = len(file_content)
    max_size_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    if max_size_bytes > 0 and file_size > max_size_bytes:
        # 检查用户是否可以绕过大小限制（管理员/运营）- 支持多角色
        user_roles = session.exec(select(UserRole).where(UserRole.user_id == current_user.id)).all()
        can_bypass = False
        for ur in user_roles:
            role = session.get(Role, ur.role_id)
            if role and (role.permissions == "*" or "video:upload_bypass" in role.permissions.split(",")):
                can_bypass = True
                break
        if not can_bypass:
            raise HTTPException(
                status_code=413,
                detail=f"文件大小超过限制 ({settings.MAX_UPLOAD_SIZE_MB}MB)"
            )

    # 保存文件
    video_uuid = str(uuid4())
    filename = f"{video_uuid}{ext}"
    file_path = settings.UPLOADS_DIR / filename

    with open(file_path, "wb") as buffer:
        buffer.write(file_content)

    # 获取分类
    category = None
    if category_id:
        category = session.get(Category, category_id)

    # 解析标签
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    # 创建视频记录
    new_video = Video(
        title=title,
        description=description,
        original_file_path=str(file_path),
        user_id=current_user.id,
        category_id=category.id if category else None,
        status="pending",
    )
    session.add(new_video)
    session.commit()
    session.refresh(new_video)

    # 处理标签
    if tag_list:
        process_tags(session, new_video, tag_list)
        session.commit()
        session.refresh(new_video)

    # 触发转码任务
    transcode_video_task.delay(str(new_video.id))

    return new_video


@router.get("/videos")
async def get_videos(
    session: Session = Depends(get_session),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    category_id: Optional[int] = None,
    keyword: Optional[str] = None,
    sort_by: str = Query("latest", enum=["latest", "popular"])
):
    """
    获取视频列表（公开）
    """
    offset = (page - 1) * size
    statement = select(Video).where(Video.status.in_(["completed", "approved"]))

    if category_id:
        statement = statement.where(Video.category_id == category_id)

    if keyword:
        statement = statement.where(Video.title.contains(keyword))

    if sort_by == "latest":
        statement = statement.order_by(Video.created_at.desc())
    else:
        statement = statement.order_by(Video.views.desc())

    videos = session.exec(statement.offset(offset).limit(size)).all()

    # 手动构建响应，确保 owner 和 category 信息完整
    result = []
    for v in videos:
        video_dict = v.model_dump()
        if v.owner:
            video_dict["owner"] = {
                "id": str(v.owner.id),
                "username": v.owner.username,
                "avatar_path": v.owner.avatar_path,
            }
        if v.category:
            video_dict["category"] = {
                "id": v.category.id,
                "name": v.category.name,
                "slug": v.category.slug,
            }
        result.append(video_dict)

    return result


@router.get("/videos/{video_id}", response_model=VideoRead)
async def get_video(
    video_id: str,
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: Session = Depends(get_session)
):
    """
    获取视频详情
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # 喜欢和收藏状态
    is_liked = False
    is_favorited = False
    if current_user:
        like = session.exec(
            select(VideoLike).where(
                VideoLike.user_id == current_user.id,
                VideoLike.video_id == video_id,
                VideoLike.like_type == "like"
            )
        ).first()
        is_liked = like is not None

        fav = session.exec(
            select(VideoFavorite).where(
                VideoFavorite.user_id == current_user.id,
                VideoFavorite.video_id == video_id
            )
        ).first()
        is_favorited = fav is not None

    video_dict = video.model_dump()
    video_dict["is_liked"] = is_liked
    video_dict["is_favorited"] = is_favorited

    # 获取视频所属的合集 ID
    item = session.exec(
        select(CollectionItem).where(CollectionItem.video_id == video.id)
    ).first()
    video_dict["collection_id"] = str(item.collection_id) if item else None

    # 添加 owner 信息
    if video.owner:
        user_roles = session.exec(select(UserRole).where(UserRole.user_id == video.owner.id)).all()
        role_ids = [ur.role_id for ur in user_roles]
        role_names = []
        for rid in role_ids:
            role = session.get(Role, rid)
            if role:
                role_names.append(role.name)
        video_dict["owner"] = {
            "id": str(video.owner.id),
            "username": video.owner.username,
            "email": video.owner.email,
            "is_active": video.owner.is_active,
            "is_admin": video.owner.is_admin,
            "role_ids": role_ids,
            "role_names": role_names,
            "created_at": video.owner.created_at,
            "avatar_path": video.owner.avatar_path,
            "bio": video.owner.bio,
        }

    return video_dict


@router.put("/videos/{video_id}", response_model=VideoRead)
async def update_video(
    video_id: str,
    video_update: VideoUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    更新视频信息
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 更新字段
    update_data = video_update.model_dump(exclude_unset=True)

    # 处理临时封面路径
    temp_thumb = update_data.pop("temp_thumbnail_path", None)
    if temp_thumb:
        # 删除旧封面
        old_thumb = settings.fs_path(video.thumbnail_path) if video.thumbnail_path else None
        if old_thumb and old_thumb.exists():
            try:
                old_thumb.unlink()
            except:
                pass
        # 更新封面路径
        video.thumbnail_path = temp_thumb

    # 处理标签（tags 是只读 property，需要单独处理）
    if "tags" in update_data:
        update_data.pop("tags")

    for key, value in update_data.items():
        setattr(video, key, value)

    # 处理标签
    if video_update.tags is not None:
        tag_list = [t.strip() for t in video_update.tags if t.strip()]
        process_tags(session, video, tag_list)

    session.add(video)
    session.commit()
    session.refresh(video)

    return video


@router.delete("/videos/{video_id}")
async def delete_video(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    删除视频（所有者或管理员）
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 删除关联记录
    from data_models import (
        TranscodeTask, Comment, VideoLike, VideoFavorite,
        VideoAuditLog, CollectionItem, VideoTag,
        RecommendationLog, UserVideoHistory, UserVideoScore
    )

    # 转码任务
    for task in session.exec(select(TranscodeTask).where(TranscodeTask.video_id == video.id)).all():
        session.delete(task)

    # 评论
    for c in session.exec(select(Comment).where(Comment.video_id == video.id)).all():
        session.delete(c)

    # 点赞
    for l in session.exec(select(VideoLike).where(VideoLike.video_id == video.id)).all():
        session.delete(l)

    # 收藏
    for f in session.exec(select(VideoFavorite).where(VideoFavorite.video_id == video.id)).all():
        session.delete(f)

    # 审核日志
    for log in session.exec(select(VideoAuditLog).where(VideoAuditLog.video_id == video.id)).all():
        session.delete(log)

    # 合集项目
    for item in session.exec(select(CollectionItem).where(CollectionItem.video_id == video.id)).all():
        session.delete(item)

    # 标签
    for tag in session.exec(select(VideoTag).where(VideoTag.video_id == video.id)).all():
        session.delete(tag)

    # 推荐日志
    for log in session.exec(select(RecommendationLog).where(RecommendationLog.video_id == video.id)).all():
        session.delete(log)

    # 观看历史
    for h in session.exec(select(UserVideoHistory).where(UserVideoHistory.video_id == video.id)).all():
        session.delete(h)

    # 评分
    for s in session.exec(select(UserVideoScore).where(UserVideoScore.video_id == video.id)).all():
        session.delete(s)

    session.delete(video)
    session.commit()

    return {"message": "Video deleted"}


@router.get("/videos/{video_id}/view-token")
async def get_view_token(
    video_id: str,
    anonymous_id: Optional[str] = Query(None),
    session: Session = Depends(get_session)
):
    """为匿名用户获取播放统计token"""
    if not anonymous_id:
        raise HTTPException(status_code=400, detail="anonymous_id required")

    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # 检查是否已有未过期的token
    from data_models import ViewToken
    existing = session.exec(
        select(ViewToken).where(
            and_(
                ViewToken.anonymous_id == anonymous_id,
                ViewToken.video_id == video_id,
                ViewToken.used == False,
                ViewToken.expires_at > datetime.utcnow()
            )
        )
    ).first()

    if existing:
        return {"token": existing.token, "expires_in": 300}

    # 创建新token（5分钟有效）
    token = ViewToken(
        token=secrets.token_urlsafe(32),
        video_id=video_id,
        anonymous_id=anonymous_id,
        expires_at=datetime.utcnow() + timedelta(minutes=5)
    )
    session.add(token)
    session.commit()

    return {"token": token.token, "expires_in": 300}


@router.post("/videos/{video_id}/view")
async def record_view(
    video_id: str,
    anonymous_id: Optional[str] = Body(None, embed=True),
    token: Optional[str] = Body(None, embed=True),
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: Session = Depends(get_session)
):
    """
    记录视频播放次数

    防刷规则：
    - 已登录用户：每视频1小时最多计1次
    - 未登录用户：每设备每视频1小时最多计1次，需有效ViewToken
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # 已登录用户：使用 UserVideoHistory 检查1小时冷却
    if current_user:
        from data_models import UserVideoHistory
        existing = session.exec(
            select(UserVideoHistory).where(
                and_(
                    UserVideoHistory.user_id == current_user.id,
                    UserVideoHistory.video_id == video_id
                )
            )
        ).first()

        if existing:
            if (datetime.utcnow() - existing.last_watched).total_seconds() < 3600:
                return {"views": video.views, "status": "cooldown"}
            existing.last_watched = datetime.utcnow()
            existing.progress = 0
            session.add(existing)
        else:
            new_record = UserVideoHistory(
                user_id=current_user.id,
                video_id=video_id,
                progress=0
            )
            session.add(new_record)

        video.views = (video.views or 0) + 1

    # 匿名用户：使用 ViewToken + AnonymousViewHistory
    elif anonymous_id and token:
        from data_models import ViewToken, AnonymousViewHistory

        # 验证token
        view_token = session.exec(
            select(ViewToken).where(
                and_(
                    ViewToken.token == token,
                    ViewToken.video_id == video_id,
                    ViewToken.anonymous_id == anonymous_id,
                    ViewToken.used == False,
                    ViewToken.expires_at > datetime.utcnow()
                )
            )
        ).first()

        if not view_token:
            return {"views": video.views, "status": "invalid_token"}

        # 标记token已用
        view_token.used = True
        session.add(view_token)

        # 检查 AnonymousViewHistory 冷却
        anon_record = session.exec(
            select(AnonymousViewHistory).where(
                and_(
                    AnonymousViewHistory.anonymous_id == anonymous_id,
                    AnonymousViewHistory.video_id == video_id
                )
            )
        ).first()

        if anon_record:
            if (datetime.utcnow() - anon_record.last_viewed_at).total_seconds() < 3600:
                return {"views": video.views, "status": "cooldown"}
            anon_record.last_viewed_at = datetime.utcnow()
            anon_record.view_count += 1
            session.add(anon_record)
        else:
            new_anon = AnonymousViewHistory(
                anonymous_id=anonymous_id,
                video_id=video_id,
                view_count=1
            )
            session.add(new_anon)

        video.views = (video.views or 0) + 1

    else:
        # 无token：不统计
        return {"views": video.views}

    session.add(video)
    session.commit()
    return {"views": video.views, "status": "ok"}


@router.post("/videos/{video_id}/progress")
async def update_progress(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    progress: float = 0,
    is_finished: bool = False
):
    """
    更新播放进度
    """
    from data_models import UserVideoHistory
    from datetime import datetime

    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    statement = select(UserVideoHistory).where(
        UserVideoHistory.user_id == current_user.id,
        UserVideoHistory.video_id == video_id
    )
    history = session.exec(statement).first()

    if history:
        history.last_watched = datetime.utcnow()
        if progress > history.progress:
            history.progress = progress
        # 标记完成状态，只记录一次完播
        if is_finished and not history.is_finished:
            history.is_finished = True
            video.complete_views = (video.complete_views or 0) + 1
            session.add(video)
        session.add(history)
    else:
        new_history = UserVideoHistory(
            user_id=current_user.id,
            video_id=video_id,
            progress=progress,
            is_finished=is_finished
        )
        session.add(new_history)
        if is_finished:
            video.complete_views = (video.complete_views or 0) + 1
            session.add(video)

    session.commit()

    return {"message": "Progress updated"}


@router.get("/videos/{video_id}/progress")
async def get_progress(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取播放进度
    """
    from data_models import UserVideoHistory

    statement = select(UserVideoHistory).where(
        UserVideoHistory.user_id == current_user.id,
        UserVideoHistory.video_id == video_id
    )
    history = session.exec(statement).first()

    return {
        "video_id": video_id,
        "watched": history is not None,
        "watched_at": history.last_watched if history else None
    }


@router.get("/users/me/history", response_model=List[Video])
async def get_watch_history(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    page: int = 1,
    size: int = 20
):
    """
    获取观看历史
    """
    from data_models import UserVideoHistory

    offset = (page - 1) * size
    history_ids = session.exec(
        select(UserVideoHistory.video_id)
        .where(UserVideoHistory.user_id == current_user.id)
        .order_by(UserVideoHistory.last_watched.desc())
        .offset(offset)
        .limit(size)
    ).all()

    if not history_ids:
        return []

    videos = session.exec(select(Video).where(Video.id.in_(history_ids))).all()

    return videos


@router.delete("/users/me/history/{video_id}", status_code=204)
async def delete_watch_history(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    删除单条观看历史
    """
    from data_models import UserVideoHistory

    history = session.exec(
        select(UserVideoHistory).where(
            UserVideoHistory.user_id == current_user.id,
            UserVideoHistory.video_id == video_id
        )
    ).first()

    if history:
        session.delete(history)
        session.commit()

    return None


@router.delete("/users/me/history", status_code=204)
async def clear_watch_history(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    清空所有观看历史
    """
    from data_models import UserVideoHistory

    session.exec(
        select(UserVideoHistory).where(UserVideoHistory.user_id == current_user.id)
    )
    histories = session.exec(
        select(UserVideoHistory).where(UserVideoHistory.user_id == current_user.id)
    ).all()

    for history in histories:
        session.delete(history)

    session.commit()
    return None


@router.post("/videos/{video_id}/like")
async def like_video(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    点赞视频
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # 检查是否已点赞
    existing = session.exec(
        select(VideoLike).where(
            VideoLike.user_id == current_user.id,
            VideoLike.video_id == video_id,
            VideoLike.like_type == "like"
        )
    ).first()

    if existing:
        return {"message": "Already liked", "liked": True}

    # 添加点赞
    like = VideoLike(
        user_id=current_user.id,
        video_id=video_id,
        like_type="like"
    )
    session.add(like)

    video.like_count = (video.like_count or 0) + 1
    session.add(video)

    session.commit()

    return {"message": "Liked", "liked": True, "total_likes": video.like_count}


@router.delete("/videos/{video_id}/like")
async def unlike_video(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    取消点赞
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    existing = session.exec(
        select(VideoLike).where(
            VideoLike.user_id == current_user.id,
            VideoLike.video_id == video_id,
            VideoLike.like_type == "like"
        )
    ).first()

    if existing:
        session.delete(existing)
        video.like_count = max(0, (video.like_count or 0) - 1)
        session.add(video)
        session.commit()

    return {"message": "Unliked", "liked": False, "total_likes": video.like_count}


@router.post("/videos/{video_id}/favorite")
async def favorite_video(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    收藏视频
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    existing = session.exec(
        select(VideoFavorite).where(
            VideoFavorite.user_id == current_user.id,
            VideoFavorite.video_id == video_id
        )
    ).first()

    if existing:
        return {"message": "Already favorited", "favorited": True}

    fav = VideoFavorite(
        user_id=current_user.id,
        video_id=video_id
    )
    session.add(fav)
    session.commit()

    return {"message": "Favorited", "favorited": True}


@router.get("/videos/{video_id}/comments")
async def get_comments(
    video_id: str,
    session: Session = Depends(get_session),
    page: int = 1,
    size: int = 20
):
    """
    获取视频评论列表
    """
    offset = (page - 1) * size
    statement = select(Comment).where(
        Comment.video_id == video_id,
        Comment.parent_id == None  # 只获取顶级评论
    ).order_by(Comment.created_at.desc())

    comments = session.exec(statement.offset(offset).limit(size)).all()

    result = []
    for c in comments:
        comment_dict = c.model_dump()
        user = session.get(User, c.user_id)
        comment_dict["user"] = {
            "id": str(user.id),
            "username": user.username,
            "display_name": user.username,
            "avatar_url": user.avatar_path
        }
        reply_count = session.exec(
            select(Comment.id).where(Comment.parent_id == c.id)
        ).all()
        comment_dict["reply_count"] = len(reply_count)
        result.append(comment_dict)

    return result


@router.post("/videos/{video_id}/comments")
async def create_comment(
    video_id: str,
    content: str = Form(...),
    parent_id: int = Form(None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    创建评论
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if parent_id:
        parent = session.get(Comment, parent_id)
        if not parent or parent.video_id != video_id:
            raise HTTPException(status_code=400, detail="Invalid parent comment")

    comment = Comment(
        video_id=video_id,
        user_id=current_user.id,
        content=content,
        parent_id=parent_id
    )
    session.add(comment)
    session.flush()

    # 发送通知
    reply_recipient = None
    comment_recipient = None

    if parent_id:
        parent_comment = session.get(Comment, parent_id)
        if parent_comment and parent_comment.user_id != current_user.id:
            notif = Notification(
                sender_id=current_user.id,
                recipient_id=parent_comment.user_id,
                type="reply",
                entity_id=f"{video_id}#{comment.id}",
                content=content[:50]
            )
            session.add(notif)
            reply_recipient = parent_comment.user_id

    if video.user_id != current_user.id:
        notif = Notification(
            sender_id=current_user.id,
            recipient_id=video.user_id,
            type="comment",
            entity_id=f"{video_id}#{comment.id}",
            content=content[:50]
        )
        session.add(notif)
        comment_recipient = video.user_id

    session.commit()
    session.refresh(comment)

    # 发布通知计数更新
    for recipient_id in set(filter(None, [reply_recipient, comment_recipient])):
        unread_count = session.exec(
            select(Notification).where(
                Notification.recipient_id == str(recipient_id),
                Notification.is_read == False
            )
        ).all()
        socketio_handler.publish_notification_count(str(recipient_id), len(unread_count), settings.REDIS_URL)

    return comment


@router.get("/comments/{comment_id}")
async def get_comment(
    comment_id: int,
    session: Session = Depends(get_session)
):
    """
    获取单条评论
    """
    comment = session.get(Comment, comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    return comment


@router.delete("/comments/{comment_id}")
async def delete_comment(
    comment_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    删除评论
    """
    comment = session.get(Comment, comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if comment.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 软删除
    comment.is_deleted = True
    session.add(comment)
    session.commit()

    return {"message": "Comment deleted"}


@router.post("/videos/{video_id}/thumbnail/regenerate")
async def regenerate_thumbnail(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    重新生成视频封面（同步执行）
    """
    import random
    import subprocess
    import time as time_module

    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        # 处理两种路径格式：完整路径 或 URL路径
        orig_path = video.original_file_path
        if orig_path.startswith("/data/myvideo"):
            input_path = Path(orig_path)
        else:
            input_path = settings.fs_path(orig_path)

        # 获取视频时长
        probe_json = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", input_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        data = json.loads(probe_json.stdout)
        total_duration = float(data['format']['duration'])

        # 随机时间点
        random_time = random.uniform(1, max(1, total_duration - 1))
        m, s = divmod(int(random_time), 60)
        h, m = divmod(m, 60)
        timestamp = f"{h:02d}:{m:02d}:{s:02d}"

        # 生成新文件名
        new_filename = f"{video.id}_{int(time_module.time())}.jpg"
        thumb_rel_path = f"/static/thumbnails/{new_filename}"
        thumb_abs_path = str(settings.THUMBNAILS_DIR / new_filename)

        # 删除旧封面
        old_thumb_path = settings.fs_path(video.thumbnail_path) if video.thumbnail_path else None
        if old_thumb_path and old_thumb_path.exists():
            try:
                old_thumb_path.unlink()
            except:
                pass

        # 生成新封面（不保存到数据库，只返回路径，等用户保存时再更新）
        subprocess.run([
            "ffmpeg", "-ss", timestamp, "-i", input_path, "-vframes", "1", thumb_abs_path, "-y"
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        return {"url": thumb_rel_path, "timestamp": timestamp}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成封面失败: {str(e)}")


@router.post("/videos/{video_id}/thumbnail/upload")
async def upload_thumbnail(
    video_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    上传视频封面（只保存文件，返回路径由用户决定是否保存）
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    # 保存文件
    ext = os.path.splitext(file.filename)[1] or ".jpg"
    filename = f"{video_id}_{int(__import__('time').time())}{ext}"
    thumb_rel_path = f"/static/thumbnails/{filename}"
    thumb_abs_path = settings.THUMBNAILS_DIR / filename

    with open(thumb_abs_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {"thumbnail_path": thumb_rel_path}


@router.get("/categories", response_model=List[Category])
async def get_categories(session: Session = Depends(get_session)):
    """
    获取所有分类
    """
    return session.exec(select(Category)).all()


@router.post("/videos/{video_id}/complete")
async def mark_video_complete(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    标记视频转码完成
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    video.status = "completed"
    video.progress = 100
    session.add(video)
    session.commit()

    return {"message": "Video marked as complete"}


@router.post("/videos/{video_id}/upgrade_priority")
async def upgrade_transcode_priority(
    video_id: str,
    priority_type: str = Query(..., description="vip_speedup 或 paid_speedup"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    升级视频转码优先级

    - vip_speedup: VIP加速（需要VIP用户）
    - paid_speedup: 付费加速（任何用户可用，扣除积分）
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # 检查视频状态
    if video.status not in ("pending", "processing"):
        raise HTTPException(status_code=400, detail="Only pending or processing videos can be upgraded")

    # 查找转码任务记录
    task = session.exec(
        select(TranscodeTask).where(
            TranscodeTask.video_id == video.id,
            TranscodeTask.status.in_(["pending", "processing"])
        )
    ).first()

    if priority_type == "vip_speedup":
        # VIP加速：需要VIP用户
        if not current_user.is_vip:
            raise HTTPException(status_code=403, detail="VIP speedup requires VIP membership")
        if task:
            task.priority = 15
            task.priority_type = "vip_speedup"
            task.queue_name = "priority"
            session.add(task)
        session.commit()
        return {"message": "Priority upgraded to VIP speedup", "priority_type": "vip_speedup"}

    elif priority_type == "paid_speedup":
        # 付费加速：扣除用户积分
        config = get_transcode_config()
        bump_cost = config.get("bump_cost", settings.TRANSCODE_BUMP_COST)

        # 检查用户积分是否足够
        if current_user.credits < bump_cost:
            raise HTTPException(status_code=403, detail=f"Insufficient credits. Need {bump_cost}, have {current_user.credits}")

        # 扣除积分
        current_user.credits -= bump_cost

        if task:
            task.priority = 30
            task.priority_type = "paid_speedup"
            task.queue_name = "priority"
            task.bump_count = (task.bump_count or 0) + 1
            session.add(task)
        session.add(current_user)
        session.commit()
        return {"message": "Priority upgraded to paid speedup", "priority_type": "paid_speedup", "credits_deducted": bump_cost}

    else:
        raise HTTPException(status_code=400, detail="Invalid priority type")


@router.post("/videos/{video_id}/bump")
async def bump_video_transcode(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    用户对自己上传的视频进行插队（消耗积分）

    - 检查用户积分是否足够
    - 扣除积分
    - 将任务设置为最高优先级
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # 检查是否是视频所有者
    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 查找待转码任务
    task = session.exec(
        select(TranscodeTask).where(
            TranscodeTask.video_id == video.id,
            TranscodeTask.status == "pending"
        )
    ).first()

    if not task:
        raise HTTPException(status_code=404, detail="No pending transcode task found")

    # 获取积分成本
    config = get_transcode_config()
    bump_cost = config.get("bump_cost", settings.TRANSCODE_BUMP_COST)

    # 检查积分
    if current_user.credits < bump_cost:
        raise HTTPException(status_code=403, detail=f"Insufficient credits. Need {bump_cost}, have {current_user.credits}")

    # 扣除积分
    current_user.credits -= bump_cost

    # 设置最高优先级
    max_priority = config["max_priority"]
    task.priority = max_priority
    task.priority_type = "paid_speedup"
    task.queue_name = "priority"
    task.bump_count = (task.bump_count or 0) + 1
    task.worker_name = None  # 清除之前的worker记录

    session.add(task)
    session.add(current_user)
    session.commit()

    return {"message": "Task bumped to priority queue", "credits_deducted": bump_cost, "new_priority": max_priority}


@router.post("/videos/{video_id}/retry")
async def retry_video_transcode(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    用户对自己上传的失败视频进行重试（仅限1次）
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # 检查是否是视频所有者
    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 查找失败的任务记录
    task = session.exec(
        select(TranscodeTask).where(
            TranscodeTask.video_id == video.id,
            TranscodeTask.status == "failed"
        )
    ).first()

    if task:
        # 检查重试次数
        if task.retry_count >= 1:
            raise HTTPException(status_code=403, detail="This task has already been retried once and cannot be retried again")
        task.retry_count += 1
        task.status = "pending"
        task.priority = 0
        task.priority_type = "normal"
        task.queue_name = "default"
        task.started_at = None
        task.completed_at = None
        session.add(task)

    video.status = "pending"
    video.progress = 0
    session.add(video)
    session.commit()

    transcode_video_task.delay(video_id)

    return {"message": "Transcode retry scheduled", "retry_count": task.retry_count if task else 0}


@router.get("/videos/{video_id}/queue_info")
async def get_video_queue_info(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取视频在转码队列中的位置和预估等待时间
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # 检查是否是视频所有者
    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 查找当前视频的转码任务
    task = session.exec(
        select(TranscodeTask).where(
            TranscodeTask.video_id == video.id,
            TranscodeTask.status.in_(["pending", "processing"])
        )
    ).first()

    if not task:
        return {"in_queue": False, "message": "No active transcode task"}

    if task.status == "processing":
        return {
            "in_queue": True,
            "status": "processing",
            "position": 0,
            "estimated_minutes": 0,
            "message": "转码中"
        }

    # 计算排在前面的任务数（相同或更高优先级的任务）
    ahead_count = session.exec(
        select(TranscodeTask).where(
            TranscodeTask.status == "pending",
            TranscodeTask.priority >= task.priority,
            TranscodeTask.created_at <= task.created_at,
            TranscodeTask.id != task.id
        )
    ).all()

    # 获取当前处理中的任务数
    processing_count = session.exec(
        select(TranscodeTask).where(TranscodeTask.status == "processing")
    ).all()

    # 粗略估算：假设每个任务平均5分钟，VIP/付费任务优先处理
    avg_task_minutes = 5
    concurrency = get_transcode_config().get("concurrency", 4)

    # 纯普通任务数量
    normal_ahead = len([t for t in ahead_count if t.priority_type == "normal"])
    priority_ahead = len(ahead_count) - normal_ahead

    # 预估时间 = VIP任务立即处理 + 普通任务排队时间
    estimated_minutes = priority_ahead * 1 + (normal_ahead // concurrency) * avg_task_minutes

    return {
        "in_queue": True,
        "status": "pending",
        "position": len(ahead_count) + 1,
        "ahead_count": len(ahead_count),
        "estimated_minutes": estimated_minutes,
        "task_priority": task.priority,
        "task_priority_type": task.priority_type,
        "current_user_credits": current_user.credits,
        "bump_cost": get_transcode_config().get("bump_cost", 5),
        "message": f"前面还有 {len(ahead_count)} 个任务"
    }


@router.post("/videos/{video_id}/appeal")
async def appeal_video(
    video_id: str,
    reason: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    用户对被下架的视频提起申诉
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    if video.status != "banned":
        raise HTTPException(status_code=400, detail="Only banned videos can be appealed")

    video.status = "appealing"
    session.add(video)

    log = VideoAuditLog(
        video_id=video_id,
        operator_id=current_user.id,
        action="appeal",
        reason=reason
    )
    session.add(log)

    session.commit()

    return {"ok": True}


@router.get("/videos/{video_id}/audit-logs")
async def get_audit_logs(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取视频的审核日志
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if not current_user.is_admin and video.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    logs = session.exec(
        select(VideoAuditLog).where(
            VideoAuditLog.video_id == video_id
        ).order_by(VideoAuditLog.created_at.desc())
    ).all()

    result = []
    for l in logs:
        op = session.get(User, l.operator_id)
        result.append({
            "action": l.action,
            "reason": l.reason,
            "created_at": l.created_at.isoformat() if l.created_at else None,
            "operator_name": op.username if op else "Unknown",
            "is_admin": op.is_admin if op else False
        })

    return result


# ==================== 分片上传接口 ====================

CHUNK_SIZE = 5 * 1024 * 1024  # 5MB per chunk


@router.post("/upload-sessions/init")
async def init_upload_session(
    filename: str = Form(...),
    file_size: int = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    category_id: int = Form(None),
    visibility: str = Form("public"),
    tags: str = Form(""),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    初始化分片上传会话
    """
    total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
    session_id = str(uuid4())
    temp_dir = settings.UPLOADS_DIR / f".chunks_{session_id}"

    # 创建临时目录
    temp_dir.mkdir(parents=True, exist_ok=True)

    # 创建会话记录
    upload_session = UploadSession(
        id=uuid4(),
        user_id=current_user.id,
        filename=filename,
        file_size=file_size,
        chunk_size=CHUNK_SIZE,
        total_chunks=total_chunks,
        uploaded_chunks=[],
        temp_dir=str(temp_dir),
        title=title,
        description=description,
        category_id=category_id,
        visibility=visibility,
        tags=tags
    )
    session.add(upload_session)
    session.commit()

    return {
        "session_id": str(upload_session.id),
        "total_chunks": total_chunks,
        "chunk_size": CHUNK_SIZE,
        "uploaded_chunks": []
    }


@router.post("/upload-sessions/{session_id}/chunks/{chunk_index}")
async def upload_chunk(
    session_id: str,
    chunk_index: int,
    chunk: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    上传单个分片
    """
    # 确保 session_id 是 UUID 格式
    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID format")

    upload_session = session.exec(
        select(UploadSession).where(UploadSession.id == session_uuid)
    ).first()

    if not upload_session:
        raise HTTPException(status_code=404, detail="Upload session not found")

    if str(upload_session.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")

    if upload_session.status != "uploading":
        raise HTTPException(status_code=400, detail="Upload session is not active")

    if chunk_index in upload_session.uploaded_chunks:
        # 分片已上传，直接返回成功
        return {"message": "Chunk already uploaded", "chunk_index": chunk_index}

    # 保存分片文件
    chunk_path = Path(upload_session.temp_dir) / f"chunk_{chunk_index}"
    with open(chunk_path, "wb") as f:
        shutil.copyfileobj(chunk.file, f)

    # 更新已上传分片
    chunks = list(upload_session.uploaded_chunks or [])
    chunks.append(chunk_index)
    chunks.sort()
    upload_session.uploaded_chunks = chunks
    upload_session.updated_at = datetime.utcnow()
    session.add(upload_session)
    session.commit()
    session.refresh(upload_session)  # 刷新确保获取最新数据

    # 计算进度并推送
    progress = len(upload_session.uploaded_chunks) / upload_session.total_chunks * 100
    socketio_handler.publish_upload_progress(
        str(current_user.id),
        str(upload_session.id),
        progress,
        len(upload_session.uploaded_chunks),
        upload_session.total_chunks,
        settings.REDIS_URL
    )

    return {
        "message": "Chunk uploaded",
        "chunk_index": chunk_index,
        "uploaded_chunks": len(upload_session.uploaded_chunks),
        "total_chunks": upload_session.total_chunks,
        "progress": progress
    }


@router.post("/upload-sessions/{session_id}/complete")
async def complete_upload_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    完成分片上传，合并所有分片并创建视频
    """
    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID format")

    upload_session = session.exec(
        select(UploadSession).where(UploadSession.id == session_uuid)
    ).first()

    if not upload_session:
        raise HTTPException(status_code=404, detail="Upload session not found")

    if str(upload_session.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")

    if upload_session.status != "uploading":
        raise HTTPException(status_code=400, detail="Upload session is not active")

    # 检查分片是否全部上传
    if len(upload_session.uploaded_chunks) != upload_session.total_chunks:
        raise HTTPException(
            status_code=400,
            detail=f"Missing chunks. Uploaded {len(upload_session.uploaded_chunks)}/{upload_session.total_chunks}"
        )

    # 合并分片
    temp_dir = Path(upload_session.temp_dir)
    final_path = settings.UPLOADS_DIR / f"{session_id}.mp4"

    with open(final_path, "wb") as outfile:
        for i in range(upload_session.total_chunks):
            chunk_path = temp_dir / f"chunk_{i}"
            with open(chunk_path, "rb") as infile:
                shutil.copyfileobj(infile, outfile)

    # 清理临时文件
    shutil.rmtree(temp_dir)

    # 创建视频记录
    video = Video(
        user_id=current_user.id,
        title=upload_session.title or upload_session.filename,
        description=upload_session.description or "",
        category_id=upload_session.category_id,
        visibility=upload_session.visibility,
        tags=upload_session.tags,
        original_file_path=f"/static/videos/uploads/{session_id}.mp4",
        status="pending",
        progress=0
    )
    session.add(video)
    session.commit()
    session.refresh(video)

    # 更新上传会话状态
    upload_session.status = "completed"
    upload_session.video_id = video.id
    upload_session.updated_at = datetime.utcnow()
    session.add(upload_session)
    session.commit()

    # 清理上传会话
    session.exec(
        select(UploadSession).where(
            UploadSession.id == session_id,
            UploadSession.status == "completed"
        )
    )
    session.commit()

    # 触发转码任务
    transcode_video_task.delay(str(video.id))

    # 推送完成事件
    socketio_handler.publish_upload_complete(
        str(current_user.id),
        str(upload_session.id),
        str(video.id),
        settings.REDIS_URL
    )

    return {
        "message": "Upload completed",
        "video_id": str(video.id),
        "status": "pending_transcode"
    }


@router.delete("/upload-sessions/{session_id}")
async def cancel_upload_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    取消上传会话，删除临时文件
    """
    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID format")

    upload_session = session.exec(
        select(UploadSession).where(UploadSession.id == session_uuid)
    ).first()

    if not upload_session:
        raise HTTPException(status_code=404, detail="Upload session not found")

    if str(upload_session.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")

    if upload_session.status != "uploading":
        raise HTTPException(status_code=400, detail="Upload session is not active")

    # 删除临时目录
    temp_dir = Path(upload_session.temp_dir)
    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    # 更新状态为已取消
    upload_session.status = "cancelled"
    session.add(upload_session)
    session.commit()

    return {"message": "Upload session cancelled"}


@router.get("/upload-sessions")
async def get_upload_sessions(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取用户正在进行的上传会话
    """
    sessions = session.exec(
        select(UploadSession).where(
            UploadSession.user_id == current_user.id,
            UploadSession.status == "uploading"
        )
    ).all()

    result = []
    for s in sessions:
        result.append({
            "session_id": str(s.id),
            "filename": s.filename,
            "file_size": s.file_size,
            "total_chunks": s.total_chunks,
            "uploaded_chunks": len(s.uploaded_chunks),
            "progress": len(s.uploaded_chunks) / s.total_chunks * 100 if s.total_chunks > 0 else 0,
            "title": s.title,
            "created_at": s.created_at.isoformat()
        })
    return result


@router.get("/upload-sessions/{session_id}")
async def get_upload_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取特定上传会话状态
    """
    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID format")

    upload_session = session.exec(
        select(UploadSession).where(UploadSession.id == session_uuid)
    ).first()

    if not upload_session:
        raise HTTPException(status_code=404, detail="Upload session not found")

    if str(upload_session.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")

    return {
        "session_id": str(upload_session.id),
        "filename": upload_session.filename,
        "file_size": upload_session.file_size,
        "total_chunks": upload_session.total_chunks,
        "uploaded_chunks": len(upload_session.uploaded_chunks),
        "uploaded_chunk_indexes": upload_session.uploaded_chunks,
        "progress": len(upload_session.uploaded_chunks) / upload_session.total_chunks * 100 if upload_session.total_chunks > 0 else 0,
        "status": upload_session.status,
        "title": upload_session.title,
        "created_at": upload_session.created_at.isoformat()
    }
