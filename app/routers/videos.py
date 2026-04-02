"""
视频相关路由
"""
from typing import List, Optional
from uuid import uuid4
import os
import shutil

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, status
from sqlmodel import Session, select

from database import get_session
from data_models import (
    Video, VideoRead, VideoUpdate, VideoLike, VideoFavorite,
    Comment, Category, User, VideoAuditLog, CollectionItem
)
from dependencies import get_current_user, get_current_user_optional, PermissionChecker, process_tags
from tasks import transcode_video_task
from config import settings

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
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="File must be a video")

    # 保存文件
    ext = os.path.splitext(file.filename)[1] or ".mp4"
    video_uuid = str(uuid4())
    filename = f"{video_uuid}{ext}"
    file_path = settings.UPLOADS_DIR / filename

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

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
        video_dict["owner"] = {
            "id": str(video.owner.id),
            "username": video.owner.username,
            "email": video.owner.email,
            "is_active": video.owner.is_active,
            "is_admin": video.owner.is_admin,
            "role_id": video.owner.role_id,
            "created_at": video.owner.created_at,
            "avatar_path": video.owner.avatar_path,
            "bio": video.owner.bio,
        }

    return video_dict


@router.put("/videos/{video_id}", response_model=VideoRead)
async def update_video(
    video_id: str,
    video_update: VideoUpdate,
    current_user: User = Depends(PermissionChecker("video:edit")),
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
    for key, value in update_data.items():
        setattr(video, key, value)

    # 处理标签
    if video_update.tags is not None:
        tag_list = [t.strip() for t in video_update.tags.split(",") if t.strip()]
        process_tags(session, video, tag_list)

    session.add(video)
    session.commit()
    session.refresh(video)

    return video


@router.delete("/videos/{video_id}")
async def delete_video(
    video_id: str,
    current_user: User = Depends(PermissionChecker("video:delete")),
    session: Session = Depends(get_session)
):
    """
    删除视频
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    session.delete(video)
    session.commit()

    return {"message": "Video deleted"}


@router.post("/videos/{video_id}/view")
async def record_view(
    video_id: str,
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: Session = Depends(get_session)
):
    """
    记录视频播放次数
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video.views = (video.views or 0) + 1
    session.add(video)
    session.commit()

    return {"views": video.views}


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

    # 手动添加 owner 信息
    result = []
    for v in videos:
        video_dict = v.model_dump()
        if v.owner:
            video_dict["owner"] = {
                "id": str(v.owner.id),
                "username": v.owner.username,
                "avatar_path": v.owner.avatar_path,
            }
        result.append(video_dict)

    return result


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
    if parent_id:
        parent_comment = session.get(Comment, parent_id)
        if parent_comment and parent_comment.user_id != current_user.id:
            from dependencies import get_current_user
            from data_models import Notification
            notif = Notification(
                sender_id=current_user.id,
                recipient_id=parent_comment.user_id,
                type="reply",
                entity_id=f"{video_id}#{comment.id}",
                content=content[:50]
            )
            session.add(notif)

    if video.user_id != current_user.id:
        from data_models import Notification
        notif = Notification(
            sender_id=current_user.id,
            recipient_id=video.user_id,
            type="comment",
            entity_id=f"{video_id}#{comment.id}",
            content=content[:50]
        )
        session.add(notif)

    session.commit()
    session.refresh(comment)

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
    current_user: User = Depends(PermissionChecker("video:edit")),
    session: Session = Depends(get_session)
):
    """
    重新生成视频封面
    """
    from tasks import regenerate_thumbnail_task

    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    regenerate_thumbnail_task.delay(video_id)

    return {"message": "Thumbnail regeneration started"}


@router.post("/videos/{video_id}/thumbnail/upload")
async def upload_thumbnail(
    video_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(PermissionChecker("video:edit")),
    session: Session = Depends(get_session)
):
    """
    上传视频封面
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
    filename = f"{video_id}{ext}"
    thumb_path = settings.THUMBNAILS_DIR / filename

    with open(thumb_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # 更新数据库
    video.thumbnail_path = f"/static/thumbnails/{filename}"
    session.add(video)
    session.commit()

    return {"thumbnail_path": video.thumbnail_path}


@router.get("/categories", response_model=List[Category])
async def get_categories(session: Session = Depends(get_session)):
    """
    获取所有分类
    """
    return session.exec(select(Category)).all()


@router.post("/videos/{video_id}/complete")
async def mark_video_complete(
    video_id: str,
    current_user: User = Depends(PermissionChecker("video:edit")),
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
