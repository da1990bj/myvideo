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
import re
import shutil
import secrets

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Body, Query, status, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select, func
from sqlalchemy import and_, asc, or_

from database import get_session
from data_models import (
    Video, VideoRead, VideoUpdate, VideoLike, VideoFavorite,
    Comment, CommentLike, Category, User, Role, UserRole, VideoAuditLog, CollectionItem,
    TranscodeTask, UploadSession, UploadSessionRead, Notification,
    SubtitleRead, SubtitleGenerateRequest, DramaSeriesItem
)
from dependencies import get_current_user, get_current_user_optional, PermissionChecker, process_tags, can_bypass_upload_limit, check_drama_upload_permission
from tasks import transcode_video_task
from config import settings, get_transcode_config, get_runtime_config
import socketio_handler

router = APIRouter(prefix="", tags=["视频"])
admin_router = APIRouter(prefix="/admin", tags=["管理后台-上传管理"])

# 支持的视频格式（模块级别常量）
ALLOWED_VIDEO_TYPES = {
    "video/mp4", "video/mpeg", "video/quicktime", "video/x-msvideo",
    "video/x-ms-wmv", "video/webm", "video/x-matroska", "video/matroska",
    "video/3gpp", "video/x-flv", "video/x-m4v", "video/ogg", "video/mp2t",
    "application/octet-stream"
}
ALLOWED_EXTENSIONS = {".mp4", ".mpeg", ".mpg", ".mov", ".avi", ".wmv", ".webm", ".mkv", ".3gp", ".flv", ".m4v", ".ogv", ".ts"}


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
    # 检查文件格式
    ext = os.path.splitext(file.filename)[1].lower() if file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的视频格式: {ext}。支持的格式: MP4, MOV, AVI, WMV, WebM, MKV, MPEG, 3GP, FLV, TS")

    # 规范化 content_type（去除参数如charset）
    content_type = file.content_type.split(";")[0].strip().lower()
    # 如果扩展名合法，则更宽松地接受 content_type（某些浏览器对MKV等格式的MIME类型判断不准确）
    # 只要扩展名在允许列表中，就通过验证
    if ext not in ALLOWED_EXTENSIONS:
        if content_type not in ALLOWED_VIDEO_TYPES:
            raise HTTPException(status_code=400, detail=f"文件类型不被支持: {content_type}")

    # 检查上传大小限制
    # 读取文件内容到内存检查大小
    file_content = await file.read()
    file_size = len(file_content)
    max_size_mb = get_runtime_config("MAX_UPLOAD_SIZE_MB", 2048)
    max_size_bytes = max_size_mb * 1024 * 1024

    if max_size_bytes > 0 and file_size > max_size_bytes:
        # 检查用户是否可以绕过大小限制（管理员/运营人员）
        if not can_bypass_upload_limit(current_user, session):
            raise HTTPException(
                status_code=413,
                detail=f"文件大小超过限制 ({max_size_mb}MB)，管理员和运营人员可绕过此限制"
            )

    # 获取分类
    category = None
    if category_id:
        category = session.get(Category, category_id)

    # 解析标签
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    # 先创建视频记录，获取 video.id
    new_video = Video(
        title=title,
        description=description,
        original_file_path="",  # 临时空值
        user_id=current_user.id,
        category_id=category.id if category else None,
        status="pending",
    )
    session.add(new_video)
    session.commit()
    session.refresh(new_video)

    # 用 video_id 命名文件
    filename = f"{new_video.id}{ext}"
    file_path = settings.UPLOADS_DIR / filename
    with open(file_path, "wb") as buffer:
        buffer.write(file_content)

    # 更新 original_file_path
    new_video.original_file_path = f"/static/videos/uploads/{filename}"
    session.add(new_video)
    session.commit()

    # 处理标签
    if tag_list:
        process_tags(session, new_video, tag_list)
        session.commit()
        session.refresh(new_video)

    # 触发转码任务（使用视频ID作为task_id防止重复）
    transcode_video_task.apply_async(args=[str(new_video.id)], task_id=str(new_video.id))

    return new_video


@router.get("/videos")
async def get_videos(
    session: Session = Depends(get_session),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    category_id: Optional[int] = None,
    keyword: Optional[str] = None,
    sort_by: str = Query("latest", enum=["latest", "popular"]),
):
    """
    获取视频列表（公开，非正剧）
    正剧视频请通过 /dramas/{type} 获取
    """
    offset = (page - 1) * size
    statement = select(Video).where(
        Video.is_approved == "approved",
        Video.status == "completed",
        Video.visibility == "public",
        Video.is_deleted == False,
        Video.series_id == None  # 排除正剧视频
    )

    if category_id:
        statement = statement.where(Video.category_id == category_id)

    if keyword:
        statement = statement.where(Video.title.contains(keyword))

    # 获取总数
    count_statement = select(func.count()).select_from(Video).where(
        Video.is_approved == "approved",
        Video.status == "completed",
        Video.visibility == "public",
        Video.is_deleted == False,
        Video.series_id == None
    )
    if category_id:
        count_statement = count_statement.where(Video.category_id == category_id)
    if keyword:
        count_statement = count_statement.where(Video.title.contains(keyword))
    total = session.exec(count_statement).one()

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

    return {"videos": result, "total": total, "page": page, "size": size}


@router.get("/videos/my")
async def get_my_videos(
    keyword: Optional[str] = Query(None, description="搜索关键词"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100)
):
    """
    获取当前用户的所有视频（支持关键词搜索）
    用于后台管理添加视频到剧集系列
    """
    offset = (page - 1) * size
    statement = select(Video).where(
        Video.user_id == current_user.id,
        Video.is_deleted == False
    )

    if keyword:
        statement = statement.where(Video.title.contains(keyword))

    statement = statement.order_by(Video.created_at.desc()).offset(offset).limit(size)
    videos = session.exec(statement).all()

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

    return {"videos": result}


@router.get("/videos/{video_id}")
async def get_video(
    video_id: str,
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: Session = Depends(get_session),
    private_token: Optional[str] = Query(None)
):
    """
    获取视频详情
    """
    video = session.get(Video, video_id)
    if not video or video.is_deleted:
        raise HTTPException(status_code=404, detail="Video not found")

    # 检查未审核视频权限
    if video.is_approved == "pending":
        # 作者本人
        is_author = current_user and current_user.id == video.user_id
        # 管理员
        is_admin = current_user and current_user.is_admin
        # 运营/审核人员
        is_staff = False
        if current_user and not is_admin:
            user_roles = session.exec(select(UserRole).where(UserRole.user_id == current_user.id)).all()
            role_ids = [ur.role_id for ur in user_roles]
            for rid in role_ids:
                role = session.get(Role, rid)
                if role and role.name in ("Operations", "Content Auditor"):
                    is_staff = True
                    break
        if not (is_author or is_admin or is_staff):
            raise HTTPException(status_code=403, detail="视频未审核")

    # 检查私密视频权限
    if video.visibility == "private":
        is_admin = current_user and current_user.is_admin
        is_owner = current_user and current_user.id == video.user_id

        # 检查私密令牌
        has_valid_private_token = False
        if private_token:
            try:
                from jose import jwt
                from config import settings
                payload = jwt.decode(private_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
                sub = payload.get("sub", "")
                # 格式: user_id:private
                if sub.endswith(":private"):
                    token_user_id = sub.split(":")[0]
                    if token_user_id == str(video.user_id):
                        has_valid_private_token = True
            except Exception:
                pass

        if not is_owner and not is_admin and not has_valid_private_token:
            raise HTTPException(status_code=403, detail="这是一条私密视频")

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

    # 添加标签
    video_dict["tags"] = video.tags

    # 检查视频是否可播放
    # 作者本人、管理员、运营/审核人员可以看非 approved 状态的视频
    # 私密视频只有作者和管理员可以看
    is_author = current_user and current_user.id == video.user_id
    is_admin = current_user and current_user.is_admin
    is_staff = False
    if current_user and not is_admin:
        user_roles = session.exec(select(UserRole).where(UserRole.user_id == current_user.id)).all()
        role_ids = [ur.role_id for ur in user_roles]
        for rid in role_ids:
            role = session.get(Role, rid)
            if role and role.name in ("Operations", "Content Auditor"):
                is_staff = True
                break
    can_view_restricted = is_author or is_admin or is_staff
    # 私密视频只有作者和管理员可以看（不需要 staff）
    can_view_private = is_author or is_admin

    video_dict["playable"] = video.status == "completed" and (
        video.is_approved == "approved" or can_view_restricted or (video.visibility == "private" and can_view_private)
    )
    video_dict["playable_message"] = None
    if not video_dict["playable"]:
        if video.is_approved == "banned" and not can_view_restricted:
            video_dict["playable_message"] = "视频已被下架"
        elif video.is_approved == "appealing" and not can_view_restricted:
            video_dict["playable_message"] = "视频审核中"
        elif video.status in ("pending", "processing"):
            video_dict["playable_message"] = "视频转码中"
        elif video.visibility == "private" and not can_view_private:
            video_dict["playable_message"] = "这是一条私密视频"
        elif video.is_approved == "banned":
            video_dict["playable_message"] = "视频已被下架（仅管理员可见）"
        elif video.is_approved == "appealing":
            video_dict["playable_message"] = "视频审核中（仅管理员可见）"

    # 添加签名后的流地址（仅对可播放的视频）
    if video.processed_file_path and video.status == "completed" and (
        video.is_approved == "approved" or can_view_restricted or (video.visibility == "private" and can_view_private)
    ):
        from security import generate_video_token
        stream_token = generate_video_token(video_id, expires_in_hours=2)
        video_dict["stream_url"] = f"/videos/{video_id}/stream?token={stream_token['token']}&expires={stream_token['expires']}"
    else:
        video_dict["stream_url"] = None

    return video_dict


@router.get("/videos/{video_id}/stream")
async def get_video_stream(
    video_id: str,
    token: str = None,
    expires: int = None,
    session: Session = Depends(get_session)
):
    """
    获取签名后的视频流地址（HLS master playlist）
    """
    from security import verify_video_token

    # 验证签名
    if not token or not expires:
        raise HTTPException(status_code=401, detail="缺少访问令牌")

    if not verify_video_token(video_id, token, expires):
        raise HTTPException(status_code=403, detail="访问令牌无效或已过期")

    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")

    if video.status != "completed":
        raise HTTPException(status_code=403, detail="视频状态异常")

    if not video.processed_file_path:
        raise HTTPException(status_code=400, detail="视频尚未处理完成")

    # 读取并修改 m3u8 内容，将 ts 路径替换为签名路径
    fs_path = settings.fs_path(video.processed_file_path)
    if not fs_path.exists():
        raise HTTPException(status_code=404, detail="视频文件不存在")

    # 读取原始 m3u8 内容
    with open(fs_path, 'r') as f:
        content = f.read()

    # 生成新的签名
    from security import generate_video_token
    new_token = generate_video_token(video_id, expires_in_hours=2)

    # 如果是 master playlist（包含 #EXTM3U），需要修改 ts 片段路径
    if "#EXTM3U" in content:
        # 这是主 playlist，修改其引用的分段路径
        lines = content.split('\n')
        new_lines = []
        for line in lines:
            if line.endswith('.ts'):
                # 生成新的分段路径
                ts_token = generate_video_token(f"{video_id}:{line}", expires_in_hours=2)
                new_line = f"/videos/{video_id}/segment?token={ts_token['token']}&expires={ts_token['expires']}&ts={line}"
                new_lines.append(new_line)
            elif line.endswith('.m3u8'):
                # 变体 playlist 也需要通过 API 访问
                variant_token = generate_video_token(f"{video_id}:{line}", expires_in_hours=2)
                new_line = f"/videos/{video_id}/variant/{line}?token={variant_token['token']}&expires={variant_token['expires']}"
                new_lines.append(new_line)
            else:
                new_lines.append(line)
        content = '\n'.join(new_lines)

    from fastapi.responses import Response
    return Response(
        content=content,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-cache"}
    )


@router.get("/videos/{video_id}/segment")
async def get_video_segment(
    video_id: str,
    token: str = None,
    expires: int = None,
    ts: str = None,
    session: Session = Depends(get_session)
):
    """
    获取视频分段（ts 文件），需要签名验证
    """
    from security import verify_video_token

    if not token or not expires or not ts:
        raise HTTPException(status_code=401, detail="缺少访问参数")

    # 验证签名（使用 ts 文件名作为额外数据）
    if not verify_video_token(f"{video_id}:{ts}", token, expires):
        raise HTTPException(status_code=403, detail="访问令牌无效或已过期")

    video = session.get(Video, video_id)
    if not video or not video.processed_file_path:
        raise HTTPException(status_code=404, detail="视频不存在")

    # 构建 ts 文件路径
    ts_dir = settings.PROCESSED_DIR / str(video_id)
    ts_path = ts_dir / ts

    if not ts_path.exists():
        raise HTTPException(status_code=404, detail="分段文件不存在")

    def file_iterator():
        with open(ts_path, 'rb') as f:
            while True:
                chunk = f.read(81920)
                if not chunk:
                    break
                yield chunk

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        file_iterator(),
        media_type="video/mp2t",
        headers={"Cache-Control": "public, max-age=3600"}
    )


@router.get("/videos/{video_id}/variant/{variant_name}")
async def get_video_variant(
    video_id: str,
    variant_name: str,
    token: str = None,
    expires: int = None,
    session: Session = Depends(get_session)
):
    """
    获取视频变体 playlist（720p.m3u8 等），需要签名验证
    """
    from security import verify_video_token, generate_video_token
    from fastapi.responses import Response

    if not token or not expires:
        raise HTTPException(status_code=401, detail="缺少访问令牌")

    if not verify_video_token(f"{video_id}:{variant_name}", token, expires):
        raise HTTPException(status_code=403, detail="访问令牌无效或已过期")

    video = session.get(Video, video_id)
    if not video or not video.processed_file_path:
        raise HTTPException(status_code=404, detail="视频不存在")

    if video.status != "completed":
        raise HTTPException(status_code=403, detail="视频状态异常")

    # 读取变体 playlist
    variant_path = settings.PROCESSED_DIR / str(video_id) / variant_name
    if not variant_path.exists():
        raise HTTPException(status_code=404, detail="变体播放列表不存在")

    with open(variant_path, 'r') as f:
        content = f.read()

    # 替换 ts 路径为签名路径
    lines = content.split('\n')
    new_lines = []
    for line in lines:
        if line.endswith('.ts'):
            ts_token = generate_video_token(f"{video_id}:{line}", expires_in_hours=2)
            new_line = f"/videos/{video_id}/segment?token={ts_token['token']}&expires={ts_token['expires']}&ts={line}"
            new_lines.append(new_line)
        else:
            new_lines.append(line)
    content = '\n'.join(new_lines)

    return Response(
        content=content,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-cache"}
    )


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
        # 去除时间戳参数（防止浏览器缓存的 ?t=xxx 被保存到数据库）
        temp_thumb = temp_thumb.split('?')[0]
        # 只有当新旧路径不同时才删除旧封面（避免删除同一文件）
        if temp_thumb != video.thumbnail_path:
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


@router.post("/videos/{video_id}/share")
async def create_video_share(
    video_id: str,
    expires_hours: Optional[int] = Query(None, description="链接有效期（小时），不填则永不过期"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    创建视频分享链接
    """
    video = session.get(Video, video_id)
    if not video or video.is_deleted:
        raise HTTPException(status_code=404, detail="Video not found")

    # 只有作者和管理员可以分享
    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 生成唯一token
    import secrets
    token = secrets.token_urlsafe(32)

    # 计算过期时间
    expires_at = None
    if expires_hours:
        expires_at = datetime.utcnow() + timedelta(hours=expires_hours)

    # 保存分享记录
    from data_models import VideoShare
    share = VideoShare(
        video_id=video.id,
        token=token,
        created_by=current_user.id,
        expires_at=expires_at
    )
    session.add(share)
    session.commit()

    # 返回分享链接 - 指向前端页面
    # 从数据库读取 SHARE_BASE_URL 配置（支持运行时修改）
    from data_models import SystemConfig
    share_base_url_config = session.exec(select(SystemConfig).where(SystemConfig.key == "SHARE_BASE_URL")).first()
    share_base_url = share_base_url_config.value if share_base_url_config and share_base_url_config.value else None

    if share_base_url:
        base_url = share_base_url.rstrip('/')
    else:
        base_url = f"http://{settings.APP_HOST}:{settings.APP_PORT}"
    share_url = f"{base_url}/static/video.html?share_token={token}"

    return {
        "share_url": share_url,
        "token": token,
        "expires_at": expires_at.isoformat() if expires_at else None
    }


@router.get("/videos/{video_id}/share")
async def get_video_share(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取视频的分享链接信息
    """
    from data_models import VideoShare

    video = session.get(Video, video_id)
    if not video or video.is_deleted:
        raise HTTPException(status_code=404, detail="Video not found")

    # 只有作者和管理员可以查看
    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 获取该视频的所有分享链接
    shares = session.exec(select(VideoShare).where(VideoShare.video_id == video.id)).all()

    if not shares:
        return {"shares": []}

    # 计算基础URL - 从数据库读取 SHARE_BASE_URL 配置
    from data_models import SystemConfig
    share_base_url_config = session.exec(select(SystemConfig).where(SystemConfig.key == "SHARE_BASE_URL")).first()
    share_base_url = share_base_url_config.value if share_base_url_config and share_base_url_config.value else None

    if share_base_url:
        base_url = share_base_url.rstrip('/')
    else:
        base_url = f"http://{settings.APP_HOST}:{settings.APP_PORT}"

    result = []
    for share in shares:
        # 检查是否过期
        is_expired = share.expires_at and share.expires_at < datetime.utcnow()
        result.append({
            "token": share.token,
            "share_url": f"{base_url}/static/video.html?share_token={share.token}",
            "expires_at": share.expires_at.isoformat() if share.expires_at else None,
            "is_expired": is_expired,
            "created_at": share.created_at.isoformat() if share.created_at else None
        })

    return {"shares": result}


@router.get("/videos/shared/{token}")
async def get_video_by_share_token(
    token: str,
    session: Session = Depends(get_session)
):
    """
    通过分享token获取视频
    """
    from data_models import VideoShare

    share = session.exec(select(VideoShare).where(VideoShare.token == token)).first()
    if not share:
        raise HTTPException(status_code=404, detail="分享链接不存在")

    # 检查是否过期
    if share.expires_at and share.expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="分享链接已过期")

    video = session.get(Video, share.video_id)
    if not video or video.is_deleted:
        raise HTTPException(status_code=404, detail="视频不存在或已删除")

    # 检查视频可见性 - 分享链接可以访问已审核的公开视频和私密视频
    if video.visibility != "public" and video.visibility != "private":
        raise HTTPException(status_code=403, detail="该视频不可访问")

    # 构建完整的视频信息
    video_dict = video.model_dump()
    video_dict["owner"] = {
        "id": str(video.owner.id),
        "username": video.owner.username,
        "avatar_path": video.owner.avatar_path,
    }
    if video.category:
        video_dict["category"] = {
            "id": video.category.id,
            "name": video.category.name,
            "slug": video.category.slug,
        }

    # 添加签名后的流地址
    if video.processed_file_path and video.status == "completed":
        from security import generate_video_token
        stream_token = generate_video_token(str(video.id), expires_in_hours=2)
        video_dict["stream_url"] = f"/videos/{video.id}/stream?token={stream_token['token']}&expires={stream_token['expires']}"
        video_dict["playable"] = True
        video_dict["playable_message"] = None
    else:
        video_dict["stream_url"] = None
        video_dict["playable"] = False
        video_dict["playable_message"] = "视频暂时无法播放"

    return video_dict


@router.delete("/videos/{video_id}/share")
async def delete_video_share(
    video_id: str,
    token: Optional[str] = Query(None, description="要删除的分享token，不填则删除该视频所有分享链接"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    删除视频的分享链接（可以删除指定token或所有）
    """
    from data_models import VideoShare

    video = session.get(Video, video_id)
    if not video or video.is_deleted:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    if token:
        # 删除指定token
        share = session.exec(select(VideoShare).where(
            VideoShare.video_id == video.id,
            VideoShare.token == token
        )).first()
        if share:
            session.delete(share)
            session.commit()
            return {"message": "分享链接已失效"}
        else:
            raise HTTPException(status_code=404, detail="分享链接不存在")
    else:
        # 删除所有分享链接
        shares = session.exec(select(VideoShare).where(VideoShare.video_id == video.id)).all()
        for share in shares:
            session.delete(share)
        session.commit()
        return {"message": f"已删除 {len(shares)} 个分享链接"}


@router.delete("/videos/{video_id}")
async def delete_video(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    删除视频（所有者或管理员）- 软删除
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 软删除：标记状态，保留数据库记录
    video.is_deleted = True
    video.deleted_at = datetime.utcnow()
    session.add(video)

    # 删除物理文件
    import shutil
    from config import settings

    if video.original_file_path:
        fp = settings.fs_path(video.original_file_path)
        if fp.exists():
            fp.unlink()
    if video.processed_file_path:
        dp = settings.fs_path(video.processed_file_path).parent
        if dp.exists():
            shutil.rmtree(dp)
    if video.thumbnail_path:
        tp = settings.fs_path(video.thumbnail_path)
        if tp.exists():
            tp.unlink()

    # 清除 Redis 缓存
    from cache_manager import get_cache
    cache = get_cache()
    cache.delete_trending_video(str(video.id))  # 从热门列表删除

    session.commit()

    return {"message": "Video deleted"}


@router.get("/video-file/{video_id}")
async def get_video_file(
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取视频文件（需要登录，流式传输）
    用于投屏等需要保护的场景
    """
    video = session.get(Video, video_id)
    if not video or not video.original_file_path:
        raise HTTPException(status_code=404, detail="Video not found")

    fs_path = settings.fs_path(video.original_file_path)
    if not fs_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    def file_iterator():
        with open(fs_path, 'rb') as f:
            while True:
                chunk = f.read(81920)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        file_iterator(),
        media_type='video/mp4',
        headers={
            'Content-Disposition': f'inline; filename="{fs_path.name}"',
            'Accept-Ranges': 'bytes'
        }
    )


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

    # 更新热门分数
    from cache_manager import get_cache
    cache = get_cache()
    cache.zincrby_trending(str(video.id), 1)  # 观看加1分
    if video.category_id:
        cache.zincrby_trending_category(video.category_id, str(video.id), 1)

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


@router.get("/users/me/history")
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
    total = session.exec(
        select(func.count()).select_from(UserVideoHistory).where(UserVideoHistory.user_id == current_user.id)
    ).one()
    history_ids = session.exec(
        select(UserVideoHistory.video_id)
        .where(UserVideoHistory.user_id == current_user.id)
        .order_by(UserVideoHistory.last_watched.desc())
        .offset(offset)
        .limit(size)
    ).all()

    if not history_ids:
        return {"videos": [], "total": 0, "page": page, "size": size}

    videos = session.exec(select(Video).where(Video.id.in_(history_ids))).all()

    return {"videos": videos, "total": total, "page": page, "size": size}


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
    点赞视频（toggle操作）
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
        # 已点赞，取消点赞
        session.delete(existing)
        video.like_count = max(0, video.like_count - 1)
        session.commit()
        return {"liked": False, "total_likes": video.like_count}

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

    # 更新热门分数
    from cache_manager import get_cache
    cache = get_cache()
    score = (video.views ** 0.5) + video.like_count * 2 + (video.favorite_count or 0) * 3
    cache.zincrby_trending(str(video.id), 2)  # 点赞加2分
    if video.category_id:
        cache.zincrby_trending_category(video.category_id, str(video.id), 2)

    return {"liked": True, "total_likes": video.like_count}


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
    收藏视频（toggle操作）
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
        # 已收藏，取消收藏
        session.delete(existing)
        session.commit()
        return {"favorited": False}

    fav = VideoFavorite(
        user_id=current_user.id,
        video_id=video_id
    )
    session.add(fav)
    video.favorite_count = (video.favorite_count or 0) + 1
    session.add(video)
    session.commit()

    # 更新热门分数
    from cache_manager import get_cache
    cache = get_cache()
    score = (video.views ** 0.5) + (video.like_count or 0) * 2 + video.favorite_count * 3
    cache.zincrby_trending(str(video.id), 3)  # 收藏加3分
    if video.category_id:
        cache.zincrby_trending_category(video.category_id, str(video.id), 3)

    return {"favorited": True}


@router.get("/videos/{video_id}/comments")
async def get_comments(
    video_id: str,
    sort: str = Query("time", description="排序方式: time 或 popular"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_optional),
    page: int = 1,
    size: int = 20
):
    """
    获取视频评论列表
    """
    offset = (page - 1) * size

    # 排序
    if sort == "popular":
        order_by = Comment.like_count.desc()
    else:
        order_by = Comment.created_at.desc()

    statement = select(Comment).where(
        Comment.video_id == video_id,
        Comment.parent_id == None  # 只获取顶级评论
    ).order_by(order_by)

    comments = session.exec(statement.offset(offset).limit(size)).all()

    # 获取用户点赞/点踩状态
    user_liked_ids = set()
    user_disliked_ids = set()
    if current_user:
        likes = session.exec(
            select(CommentLike).where(
                CommentLike.user_id == current_user.id,
                CommentLike.like_type == "like"
            )
        ).all()
        dislikes = session.exec(
            select(CommentLike).where(
                CommentLike.user_id == current_user.id,
                CommentLike.like_type == "dislike"
            )
        ).all()
        user_liked_ids = set(l.comment_id for l in likes)
        user_disliked_ids = set(d.comment_id for d in dislikes)

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
        comment_dict["is_liked"] = c.id in user_liked_ids
        comment_dict["is_disliked"] = c.id in user_disliked_ids
        reply_count = session.exec(
            select(Comment.id).where(Comment.parent_id == c.id)
        ).all()
        comment_dict["reply_count"] = len(reply_count)
        # 已删除的评论替换内容
        if c.is_deleted:
            if c.deleted_by == "admin":
                comment_dict["content"] = "该评论已由系统删除"
            else:
                comment_dict["content"] = "该评论已由用户删除"
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
    comment.deleted_by = "admin" if current_user.is_admin else "user"
    session.add(comment)
    session.commit()

    return {"message": "Comment deleted"}


@router.post("/comments/{comment_id}/like")
async def like_comment(
    comment_id: int,
    like_type: str = Form("like"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    点赞/点踩评论（同一用户对同一评论只能选择like或dislike之一，互斥）
    like_type: "like" 或 "dislike"
    """
    comment = session.get(Comment, comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if like_type not in ("like", "dislike"):
        raise HTTPException(status_code=400, detail="Invalid like type")

    # 检查用户是否已对这条评论点赞/点踩
    existing = session.exec(
        select(CommentLike).where(
            CommentLike.comment_id == comment_id,
            CommentLike.user_id == current_user.id
        )
    ).first()

    if existing:
        if existing.like_type == like_type:
            # 点同样的按钮，取消点赞/点踩
            session.delete(existing)
            session.commit()
            liked = False
        else:
            # 点不同的按钮，把另一个删掉，添加新的
            session.delete(existing)
            new_like = CommentLike(comment_id=comment_id, user_id=current_user.id, like_type=like_type)
            session.add(new_like)
            session.commit()
            liked = True
    else:
        # 没有点赞/点踩过，直接添加
        new_like = CommentLike(comment_id=comment_id, user_id=current_user.id, like_type=like_type)
        session.add(new_like)
        session.commit()
        liked = True

    # 从数据库重新计算实际数量
    actual_likes = session.exec(
        select(CommentLike.comment_id).where(
            CommentLike.comment_id == comment_id,
            CommentLike.like_type == "like"
        )
    ).all()
    actual_dislikes = session.exec(
        select(CommentLike.comment_id).where(
            CommentLike.comment_id == comment_id,
            CommentLike.like_type == "dislike"
        )
    ).all()

    comment.like_count = len(actual_likes)
    comment.dislike_count = len(actual_dislikes)
    session.add(comment)
    session.commit()

    return {"liked": liked, "like_count": comment.like_count, "dislike_count": comment.dislike_count}


@router.delete("/comments/{comment_id}/like")
async def unlike_comment(
    comment_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    取消点赞/点踩评论
    """
    comment = session.get(Comment, comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    existing = session.exec(
        select(CommentLike).where(
            CommentLike.comment_id == comment_id,
            CommentLike.user_id == current_user.id
        )
    ).first()

    if existing:
        if existing.like_type == "like":
            comment.like_count = max(0, comment.like_count - 1)
        session.delete(existing)
        session.commit()

    return {"liked": False, "like_count": comment.like_count}


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
        new_filename = f"{video.id}.jpg"
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
        result = subprocess.run([
            "ffmpeg", "-ss", timestamp, "-i", input_path, "-vframes", "1", "-update", "1", thumb_abs_path, "-y"
        ], capture_output=True, text=True)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"生成封面失败: {result.stderr}")

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
    filename = f"{video_id}.jpg"
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
    return session.exec(select(Category).order_by(asc(Category.display_order))).all()


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

    transcode_video_task.apply_async(args=[video_id], task_id=video_id)

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

    if video.is_approved != "banned":
        raise HTTPException(status_code=400, detail="Only banned videos can be appealed")

    video.is_approved = "appealing"
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


@router.get("/upload-config")
async def get_upload_config():
    """
    获取上传相关配置（公开接口）
    """
    return {
        "chunk_size": CHUNK_SIZE,
        "concurrency": get_runtime_config("UPLOAD_CONCURRENCY", 3),
        "max_upload_size_mb": get_runtime_config("MAX_UPLOAD_SIZE_MB", 2048),
    }


@router.post("/upload-sessions/init")
async def init_upload_session(
    filename: str = Form(...),
    file_size: int = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    category_id: Optional[str] = Form(None),
    visibility: str = Form("public"),
    tags: str = Form(""),
    series_id: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    初始化分片上传会话
    """
    # 检查文件扩展名
    ext = os.path.splitext(filename)[1].lower() if filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的视频格式: {ext}。支持的格式: MP4, MOV, AVI, WMV, WebM, MKV, MPEG, 3GP, FLV, TS"
        )

    # 转换 category_id 为 int（前端可能传字符串）
    if category_id:
        try:
            category_id = int(category_id)
        except (ValueError, TypeError):
            category_id = None

    # 转换 series_id 为 UUID（前端传字符串）
    if series_id and not isinstance(series_id, UUID):
        try:
            series_id = UUID(series_id)
        except (ValueError, TypeError):
            series_id = None

    # 检查上传大小限制（管理员和运营人员除外）
    if not can_bypass_upload_limit(current_user, session):
        max_size_mb = get_runtime_config("MAX_UPLOAD_SIZE_MB", "2048")
        max_size_bytes = int(max_size_mb) * 1024 * 1024
        if max_size_bytes > 0 and file_size > max_size_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"文件大小超过限制 {max_size_mb}MB，管理员和运营人员可绕过此限制"
            )

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
        tags=tags,
        series_id=series_id if isinstance(series_id, UUID) else (UUID(series_id) if series_id else None)
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
        raise HTTPException(status_code=400, detail=f"Upload session is not active: status={upload_session.status}")

    # 检查分片是否全部上传
    if len(upload_session.uploaded_chunks) != upload_session.total_chunks:
        raise HTTPException(
            status_code=400,
            detail=f"Missing chunks. Uploaded {len(upload_session.uploaded_chunks)}/{upload_session.total_chunks}"
        )

    # 先创建视频记录，获取 video.id
    video = Video(
        user_id=current_user.id,
        title=upload_session.title or upload_session.filename,
        description=upload_session.description or "",
        category_id=upload_session.category_id,
        visibility=upload_session.visibility,
        tags=upload_session.tags,
        original_file_path="",  # 临时空值
        status="pending",
        progress=0,
        series_id=upload_session.series_id
    )
    session.add(video)
    session.commit()
    session.refresh(video)

    # 用 video_id 合并分片
    temp_dir = Path(upload_session.temp_dir)
    final_path = settings.UPLOADS_DIR / f"{video.id}.mp4"

    with open(final_path, "wb") as outfile:
        for i in range(upload_session.total_chunks):
            chunk_path = temp_dir / f"chunk_{i}"
            with open(chunk_path, "rb") as infile:
                shutil.copyfileobj(infile, outfile)

    # 清理临时文件
    shutil.rmtree(temp_dir)

    # 更新 original_file_path
    video.original_file_path = f"/static/videos/uploads/{video.id}.mp4"
    session.add(video)

    # 如果上传时指定了剧集系列，同时创建 DramaSeriesItem 条目
    if upload_session.series_id:
        # 获取当前最大 order
        max_order = session.exec(
            select(DramaSeriesItem.order).where(DramaSeriesItem.series_id == upload_session.series_id)
            .order_by(DramaSeriesItem.order.desc())
        ).first()
        next_order = (max_order or 0) + 1

        series_item = DramaSeriesItem(
            series_id=upload_session.series_id,
            video_id=video.id,
            order=next_order
        )
        session.add(series_item)

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

    # 触发转码任务（使用视频ID作为task_id防止重复）
    transcode_video_task.apply_async(args=[str(video.id)], task_id=str(video.id))

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

    # 更新状态为已取消，保留记录用于统计
    upload_session.status = "cancelled"
    upload_session.uploaded_chunks = []
    session.add(upload_session)
    session.commit()

    return {"message": "Upload session cancelled", "status": "cancelled"}


@router.get("/upload-sessions")
async def get_upload_sessions(
    series_id: Optional[str] = Query(None, description="剧集系列ID过滤"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取用户正在进行的上传会话
    """
    query = select(UploadSession).where(
        UploadSession.user_id == current_user.id,
        UploadSession.status == "uploading"
    )
    if series_id:
        query = query.where(UploadSession.series_id == UUID(series_id))

    sessions = session.exec(query).all()

    result = []
    for s in sessions:
        # 判断是否超时中断（上传中但5分钟无更新）
        is_stale = False
        if s.status == "uploading" and s.updated_at:
            from datetime import datetime, timedelta
            stale_threshold = datetime.utcnow() - timedelta(minutes=5)
            if s.updated_at < stale_threshold:
                is_stale = True

        result.append({
            "session_id": str(s.id),
            "filename": s.filename,
            "file_size": s.file_size,
            "total_chunks": s.total_chunks,
            "uploaded_chunks": len(s.uploaded_chunks) if s.uploaded_chunks else 0,
            "uploaded_chunk_indexes": s.uploaded_chunks or [],
            "progress": len(s.uploaded_chunks) / s.total_chunks * 100 if s.total_chunks > 0 else 0,
            "title": s.title,
            "series_id": str(s.series_id) if s.series_id else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "is_stale": is_stale
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


# ============ 字幕相关 ============

def convert_srt_to_vtt(srt_content: str) -> str:
    """Convert SRT subtitle format to VTT"""
    vtt = "WEBVTT\n\n"
    srt_content = srt_content.lstrip('\ufeff')

    def fix_timestamp(match):
        ts = match.group(0).replace(',', '.')
        return ts

    blocks = re.split(r'\n\s*\n', srt_content.strip())
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) >= 2:
            timestamp_line = lines[1]
            timestamp_line = re.sub(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})',
                                    r'\1:\2:\3.\4', timestamp_line)
            timestamp_line = timestamp_line.replace(' --> ', ' --> ')
            vtt += timestamp_line + '\n'
            vtt += '\n'.join(lines[2:]) + '\n\n'
    return vtt


@router.post("/videos/{video_id}/subtitles", response_model=SubtitleRead)
async def upload_subtitle(
    video_id: str,
    file: UploadFile = File(...),
    language: str = Form(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """上传字幕文件 (VTT/SRT)"""
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    if not video.processed_file_path:
        raise HTTPException(status_code=400, detail="Video not yet processed")

    ext = os.path.splitext(file.filename)[1].lower() if file.filename else ""
    if ext not in {".vtt", ".srt"}:
        raise HTTPException(status_code=400, detail="Only .vtt and .srt files are supported")

    subtitle_dir = settings.PROCESSED_DIR / str(video_id) / "subtitles"
    subtitle_dir.mkdir(parents=True, exist_ok=True)

    subtitle_path = subtitle_dir / f"{language}.vtt"
    content = await file.read()

    if ext == ".srt":
        content = convert_srt_to_vtt(content.decode('utf-8')).encode('utf-8')

    with open(subtitle_path, "wb") as f:
        f.write(content)

    languages = video.subtitle_languages or []
    if language not in languages:
        languages.append(language)
        video.subtitle_languages = languages
        session.add(video)
        session.commit()

    subtitle_url = f"/static/videos/processed/{video_id}/subtitles/{language}.vtt"
    return SubtitleRead(language=language, url=subtitle_url, is_auto_generated=False)


@router.get("/videos/{video_id}/subtitles", response_model=List[SubtitleRead])
async def get_subtitles(
    video_id: str,
    session: Session = Depends(get_session)
):
    """获取视频的所有字幕"""
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    subtitles = []
    subtitle_dir = settings.PROCESSED_DIR / str(video_id) / "subtitles"
    if subtitle_dir.exists():
        # 获取该视频的所有字幕文件
        vtt_files = list(subtitle_dir.glob("*.vtt"))
        for vtt_file in vtt_files:
            # 提取语言代码（文件名去掉.vtt扩展名）
            stem = vtt_file.stem  # 例如 "eng", "zh-Hans", "eng_english", "track6_unknown" 等
            # 处理 track{N}_unknown 格式，提取真正的语言代码
            if stem.startswith("track") and "_unknown" in stem:
                lang = stem.split("_unknown")[0]  # "track6_unknown" -> "track6"
            # 处理 track{N}_{lang} 格式，提取语言代码
            elif stem.startswith("track") and "_" in stem:
                parts = stem.split("_")
                if len(parts) >= 2:
                    lang = parts[1]  # "track18_heb" -> "heb"
                else:
                    lang = stem
            else:
                lang = stem
            url = f"/static/videos/processed/{video_id}/subtitles/{vtt_file.name}"
            # 检查是否自动生成（通过文件名匹配）
            is_auto = video.auto_subtitle and video.auto_subtitle_language == lang
            subtitles.append(SubtitleRead(
                language=lang,
                url=url,
                is_auto_generated=is_auto
            ))
    return subtitles


@router.delete("/videos/{video_id}/subtitles/{language}")
async def delete_subtitle(
    video_id: str,
    language: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """删除指定语言的字幕"""
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    subtitle_dir = settings.PROCESSED_DIR / str(video_id) / "subtitles"
    # 查找匹配语言的 VTT 文件（可能名为 {lang}.vtt 或 track{index}_{lang}.vtt）
    vtt_files = list(subtitle_dir.glob(f"*{language}*.vtt")) if subtitle_dir.exists() else []
    for vtt_file in vtt_files:
        vtt_file.unlink()

    m3u8_files = list(subtitle_dir.glob(f"*{language}*.m3u8")) if subtitle_dir.exists() else []
    for m3u8_file in m3u8_files:
        m3u8_file.unlink()

    if video.subtitle_languages and language in video.subtitle_languages:
        video.subtitle_languages.remove(language)
        if video.auto_subtitle_language == language:
            video.auto_subtitle_language = None
            video.auto_subtitle = False
        session.add(video)
        session.commit()

    return {"message": "Subtitle deleted"}


@router.post("/videos/{video_id}/subtitles/generate")
async def generate_subtitles(
    video_id: str,
    req: SubtitleGenerateRequest = Body(SubtitleGenerateRequest(language="en")),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """使用 Whisper AI 自动生成字幕"""
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    if not video.processed_file_path:
        raise HTTPException(status_code=400, detail="Video not yet processed")

    from tasks import generate_subtitle_task
    import logging
    logger = logging.getLogger(__name__)

    task = generate_subtitle_task.delay(str(video_id), req.language)
    print(f"DEBUG: Task created, task_id={task.id}", flush=True)

    # 直接用 SQLAlchemy engine 执行
    from database import engine
    with engine.connect() as conn:
        from sqlalchemy import text
        conn.execute(
            text("UPDATE videos SET subtitle_task_id = :tid WHERE id = :vid"),
            {"tid": task.id, "vid": video_id}
        )
        conn.commit()
        print(f"DEBUG: Update committed", flush=True)

    # 立即查询验证
    with engine.connect() as conn:
        from sqlalchemy import text
        result = conn.execute(text("SELECT subtitle_task_id FROM videos WHERE id = :vid"), {"vid": video_id})
        row = result.fetchone()
        print(f"DEBUG: Immediately after update, subtitle_task_id = {row[0] if row else 'NULL'}", flush=True)

    return {"message": "Subtitle generation started", "language": req.language, "task_id": task.id}

    return {"message": "Subtitle generation started", "language": req.language, "task_id": task.id}


@router.get("/videos/{video_id}/subtitles/task-status")
async def get_subtitle_task_status(
    video_id: str,
    session: Session = Depends(get_session)
):
    """查询字幕生成任务状态"""
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    task_id = video.subtitle_task_id
    if not task_id:
        return {"status": "none", "message": "No subtitle task running"}

    from celery.result import AsyncResult
    task_result = AsyncResult(task_id)

    if task_result.ready():
        # 任务已完成
        video.subtitle_task_id = None
        session.add(video)
        session.commit()
        if task_result.successful():
            return {"status": "completed", "task_id": task_id}
        else:
            return {"status": "failed", "task_id": task_id, "error": str(task_result.result)}
    else:
        # 任务进行中
        return {"status": "processing", "task_id": task_id}


# ==================== 管理员上传会话管理 ====================

@admin_router.get("/upload-sessions")
async def admin_get_upload_sessions(
    status: Optional[str] = Query(None, description="过滤状态: uploading, completed, cancelled"),
    series_id: Optional[str] = Query(None, description="剧集系列ID"),
    user_id: Optional[str] = Query(None, description="用户ID"),
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super")),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200)
):
    """
    获取所有上传会话（管理员）
    """
    query = select(UploadSession)

    if status:
        query = query.where(UploadSession.status == status)
    if series_id:
        query = query.where(UploadSession.series_id == UUID(series_id))
    if user_id:
        query = query.where(UploadSession.user_id == UUID(user_id))

    # 获取总数
    count_query = select(UploadSession)
    if status:
        count_query = count_query.where(UploadSession.status == status)
    if series_id:
        count_query = count_query.where(UploadSession.series_id == UUID(series_id))
    if user_id:
        count_query = count_query.where(UploadSession.user_id == UUID(user_id))
    total = len(session.exec(count_query).all())

    query = query.order_by(UploadSession.created_at.desc())
    query = query.offset((page - 1) * size).limit(size)
    sessions = session.exec(query).all()

    result = []
    for s in sessions:
        # 获取用户名
        user = session.get(User, s.user_id)
        username = user.username if user else "未知"

        # 获取剧集系列标题
        series_title = None
        if s.series_id:
            from data_models import DramaSeries
            series = session.get(DramaSeries, s.series_id)
            if series:
                series_title = series.title

        # 获取分类名称
        category_name = None
        if s.category_id:
            category = session.get(Category, s.category_id)
            if category:
                category_name = category.name

        # 判断是否超时中断（上传中但5分钟无更新）
        is_stale = False
        if s.status == "uploading" and s.updated_at:
            from datetime import datetime, timedelta
            stale_threshold = datetime.utcnow() - timedelta(minutes=5)
            if s.updated_at < stale_threshold:
                is_stale = True

        result.append({
            "session_id": str(s.id),
            "user_id": str(s.user_id),
            "username": username,
            "filename": s.filename,
            "file_size": s.file_size,
            "total_chunks": s.total_chunks,
            "uploaded_chunks": len(s.uploaded_chunks) if s.uploaded_chunks else 0,
            "uploaded_chunk_indexes": s.uploaded_chunks or [],
            "progress": len(s.uploaded_chunks) / s.total_chunks * 100 if s.total_chunks > 0 else 0,
            "status": s.status,
            "title": s.title,
            "category_id": s.category_id,
            "category_name": category_name,
            "series_id": str(s.series_id) if s.series_id else None,
            "series_title": series_title,
            "video_id": str(s.video_id) if s.video_id else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            "is_stale": is_stale
        })

    return {"sessions": result, "total": total, "page": page, "size": size}


@admin_router.delete("/upload-sessions/{session_id}")
async def admin_cancel_upload_session(
    session_id: str,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    管理员取消上传会话
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

    # 删除临时目录
    temp_dir = Path(upload_session.temp_dir)
    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    # 更新状态为已取消
    upload_session.status = "cancelled"
    session.add(upload_session)
    session.commit()

    return {"message": "Upload session cancelled"}


# ==================== 管理员视频搜索 ====================

@admin_router.get("/videos/search")
async def admin_search_videos(
    keyword: Optional[str] = Query(None, description="搜索关键词"),
    status: Optional[str] = Query(None, description="视频状态筛选"),
    series_id: Optional[str] = Query(None, description="剧集系列ID筛选"),
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super")),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100)
):
    """
    管理员搜索视频（用于添加视频到剧集系列）
    """
    offset = (page - 1) * size
    statement = select(Video).where(Video.is_deleted == False)

    if keyword:
        statement = statement.where(Video.title.contains(keyword))
    if status:
        statement = statement.where(Video.status == status)
    if series_id:
        statement = statement.where(Video.series_id == UUID(series_id))

    # 获取总数
    count_statement = select(func.count()).select_from(Video).where(Video.is_deleted == False)
    if keyword:
        count_statement = count_statement.where(Video.title.contains(keyword))
    if status:
        count_statement = count_statement.where(Video.status == status)
    if series_id:
        count_statement = count_statement.where(Video.series_id == UUID(series_id))
    total = session.exec(count_statement).one()

    statement = statement.order_by(Video.created_at.desc()).offset(offset).limit(size)
    videos = session.exec(statement).all()

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

    return {"videos": result, "total": total, "page": page, "size": size}
