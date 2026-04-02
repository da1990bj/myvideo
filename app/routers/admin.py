"""
管理后台路由
"""
from typing import List, Optional
from uuid import uuid4
from datetime import datetime
import psutil
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlmodel import Session, select, desc, func

from database import get_session, engine
from data_models import (
    User, UserRead, Video, VideoRead, Role, SystemConfig, AdminLog,
    VideoAuditLog, Comment, Notification, Category,
    VideoRecommendation, VideoRecommendationWithVideoRead,
    RecommendationSlot, RecommendationSlotRead, RecommendationLog, UserVideoScore
)
from dependencies import get_current_user, PermissionChecker, log_admin_action
from tasks import transcode_video_task, migrate_storage_task
from config import settings

router = APIRouter(prefix="/admin", tags=["管理后台"])


# ==================== 统计相关 ====================

@router.get("/stats/system")
async def get_system_stats(session: Session = Depends(get_session)):
    """
    获取系统级别的统计数据
    """
    try:
        users = session.exec(select(func.count()).select_from(User)).one()
        videos = session.exec(select(func.count()).select_from(Video)).one()
        comments = session.exec(select(func.count()).select_from(Comment)).one()
    except Exception:
        users, videos, comments = 0, 0, 0

    try:
        processing = session.exec(select(Video).where(Video.status == "processing")).all()
        pending = session.exec(select(Video).where(Video.status == "pending")).all()
    except Exception:
        processing, pending = [], []

    # 计算总存储
    total_size = 0
    for dir_path in [settings.UPLOADS_DIR, settings.PROCESSED_DIR, settings.THUMBNAILS_DIR]:
        if dir_path.exists():
            for entry in dir_path.rglob("*"):
                if entry.is_file():
                    total_size += entry.stat().st_size

    return {
        "total_users": users,
        "total_videos": videos,
        "total_comments": comments,
        "processing_videos": len(processing),
        "pending_videos": len(pending),
        "storage_used": total_size,
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory": {
            "percent": psutil.virtual_memory().percent,
            "total": psutil.virtual_memory().total,
            "available": psutil.virtual_memory().available,
        },
        "disk": {
            "percent": psutil.disk_usage('/').percent,
            "total": psutil.disk_usage('/').total,
            "free": psutil.disk_usage('/').free,
        },
    }


@router.get("/stats")
async def get_admin_stats(
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """
    获取管理员仪表盘统计数据
    """
    users = session.exec(select(func.count()).select_from(User)).one()
    videos = session.exec(select(func.count()).select_from(Video)).one()
    comments = session.exec(select(func.count()).select_from(Comment)).one()

    pending = session.exec(select(Video).where(Video.status == "pending").order_by(Video.created_at)).all()
    processing = session.exec(select(Video).where(Video.status == "processing").order_by(Video.created_at)).all()
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_completed = session.exec(
        select(Video).where(Video.status == "completed", Video.created_at >= today)
    ).all()
    failed = session.exec(
        select(Video).where(Video.status == "failed").order_by(desc(Video.created_at)).limit(20)
    ).all()

    return {
        "users": users,
        "videos": videos,
        "comments": comments,
        "pending_videos": pending,
        "processing_videos": processing,
        "today_completed": today_completed,
        "recently_failed": failed,
    }


# ==================== 角色管理 ====================

@router.get("/roles")
async def get_roles(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """获取所有角色"""
    return session.exec(select(Role)).all()


@router.post("/roles")
async def create_role(
    name: str = Body(...),
    description: str = Body(""),
    permissions: str = Body("", embed=True),
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """创建新角色"""
    existing = session.exec(select(Role).where(Role.name == name)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Role already exists")

    role = Role(name=name, description=description, permissions=permissions)
    session.add(role)
    session.commit()
    session.refresh(role)

    log_admin_action(session, admin.id, "create_role", str(role.id), name)
    session.commit()

    return role


@router.put("/roles/{role_id}")
async def update_role(
    role_id: str,
    permissions: str = Body(..., embed=True),
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """更新角色权限"""
    role = session.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    role.permissions = permissions
    session.add(role)
    session.commit()

    log_admin_action(session, admin.id, "update_role", str(role_id), f"Permissions: {permissions}")
    session.commit()

    return role


# ==================== 系统配置 ====================

def get_config_override(key: str, default: any, session: Session) -> any:
    """从SystemConfig获取配置覆盖值"""
    config = session.exec(select(SystemConfig).where(SystemConfig.key == key)).first()
    if config:
        # 尝试转换类型
        if isinstance(default, bool):
            return config.value.lower() in ("true", "1", "yes")
        elif isinstance(default, int):
            return int(config.value)
        elif isinstance(default, float):
            return float(config.value)
        return config.value
    return default

@router.get("/config")
async def get_config(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """获取所有系统配置"""
    configs = session.exec(select(SystemConfig)).all()
    return {c.key: c.value for c in configs}


@router.put("/config")
async def update_config(
    key: str = Body(...),
    value: str = Body(...),
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """更新系统配置"""
    conf = session.get(SystemConfig, key)
    if conf:
        conf.value = value
        session.add(conf)
    else:
        conf = SystemConfig(key=key, value=value)
        session.add(conf)

    log_admin_action(session, admin.id, "update_config", key, f"Set to {value}")
    session.commit()

    return {"message": "Config updated"}


@router.get("/env-config")
async def get_env_config(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session),
    exclude: Optional[str] = Query(None)
):
    """获取环境配置（敏感）"""
    # 配置分组定义
    GROUPS = {
        "基础配置": ["MYVIDEO_ROOT"],
        "数据库": ["DATABASE_HOST", "DATABASE_PORT", "DATABASE_USER", "DATABASE_PASSWORD", "DATABASE_NAME", "DATABASE_ECHO"],
        "Redis": ["REDIS_HOST", "REDIS_PORT", "REDIS_DB", "REDIS_PASSWORD"],
        "安全/JWT": ["SECRET_KEY", "ALGORITHM", "ACCESS_TOKEN_EXPIRE_MINUTES"],
        "应用服务器": ["APP_HOST", "APP_PORT", "APP_DEBUG"],
        "文件存储目录": ["STATIC_SUBDIR", "UPLOADS_SUBDIR", "PROCESSED_SUBDIR", "THUMBNAILS_SUBDIR", "AVATARS_SUBDIR", "DATA_SUBDIR"],
        "URL路径前缀": ["THUMBNAILS_URL", "VIDEOS_URL", "AVATARS_URL"],
        "CORS": ["CORS_ORIGINS", "CORS_CREDENTIALS", "CORS_METHODS", "CORS_HEADERS"],
        "Celery": ["CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"],
        "日志": ["LOG_LEVEL", "LOG_FILE"],
        "敏感词": ["SENSITIVE_WORDS_FILE"],
        "冷存储": ["COLD_STORAGE_ENABLED", "COLD_STORAGE_TRIGGER_DAYS", "COLD_STORAGE_TRIGGER_VIEWS", "COLD_STORAGE_PATH_ROOT"],
        "存储迁移": ["STORAGE_MIGRATION_DELAY"],
    }

    # 构建配置数据
    config_data = {
        "基础配置": {"MYVIDEO_ROOT": settings.MYVIDEO_ROOT},
        "数据库": {
            "DATABASE_HOST": settings.DATABASE_HOST,
            "DATABASE_PORT": settings.DATABASE_PORT,
            "DATABASE_USER": settings.DATABASE_USER,
            "DATABASE_PASSWORD": "****" if settings.DATABASE_PASSWORD else "",
            "DATABASE_NAME": settings.DATABASE_NAME,
            "DATABASE_ECHO": settings.DATABASE_ECHO,
        },
        "Redis": {
            "REDIS_HOST": settings.REDIS_HOST,
            "REDIS_PORT": settings.REDIS_PORT,
            "REDIS_DB": settings.REDIS_DB,
            "REDIS_PASSWORD": "****" if settings.REDIS_PASSWORD else "",
        },
        "安全/JWT": {
            "SECRET_KEY": "****" if settings.SECRET_KEY else "",
            "ALGORITHM": settings.ALGORITHM,
            "ACCESS_TOKEN_EXPIRE_MINUTES": get_config_override("ACCESS_TOKEN_EXPIRE_MINUTES", settings.ACCESS_TOKEN_EXPIRE_MINUTES, session),
        },
        "应用服务器": {
            "APP_HOST": settings.APP_HOST,
            "APP_PORT": settings.APP_PORT,
            "APP_DEBUG": settings.APP_DEBUG,
        },
        "文件存储目录": {
            "STATIC_SUBDIR": settings.STATIC_SUBDIR,
            "UPLOADS_SUBDIR": settings.UPLOADS_SUBDIR,
            "PROCESSED_SUBDIR": settings.PROCESSED_SUBDIR,
            "THUMBNAILS_SUBDIR": settings.THUMBNAILS_SUBDIR,
            "AVATARS_SUBDIR": settings.AVATARS_SUBDIR,
            "DATA_SUBDIR": settings.DATA_SUBDIR,
        },
        "URL路径前缀": {
            "THUMBNAILS_URL": settings.THUMBNAILS_URL,
            "VIDEOS_URL": settings.VIDEOS_URL,
            "AVATARS_URL": settings.AVATARS_URL,
        },
        "CORS": {
            "CORS_ORIGINS": settings.CORS_ORIGINS,
            "CORS_CREDENTIALS": settings.CORS_CREDENTIALS,
            "CORS_METHODS": settings.CORS_METHODS,
            "CORS_HEADERS": settings.CORS_HEADERS,
        },
        "Celery": {
            "CELERY_BROKER_URL": settings.CELERY_BROKER or "",
            "CELERY_RESULT_BACKEND": settings.CELERY_RESULT_BACKEND or "",
        },
        "日志": {
            "LOG_LEVEL": settings.LOG_LEVEL,
            "LOG_FILE": str(settings.LOG_FILE) if settings.LOG_FILE else "",
        },
        "敏感词": {
            "SENSITIVE_WORDS_FILE": str(settings.SENSITIVE_WORDS_PATH) if settings.SENSITIVE_WORDS_FILE else "",
        },
        "冷存储": {
            "COLD_STORAGE_ENABLED": settings.COLD_STORAGE_ENABLED,
            "COLD_STORAGE_TRIGGER_DAYS": settings.COLD_STORAGE_TRIGGER_DAYS,
            "COLD_STORAGE_TRIGGER_VIEWS": settings.COLD_STORAGE_TRIGGER_VIEWS,
            "COLD_STORAGE_PATH_ROOT": str(settings.COLD_STORAGE_PATH),
        },
        "存储迁移": {
            "STORAGE_MIGRATION_DELAY": settings.STORAGE_MIGRATION_DELAY,
        },
    }

    # 处理排除
    if exclude:
        exclude_list = [s.strip() for s in exclude.split(",")]
        for section in exclude_list:
            if section in config_data:
                del config_data[section]

    return config_data


@router.put("/env-config")
async def update_env_config(
    updates: dict = Body(...),
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """更新环境配置（支持运行时修改的配置项）"""
    # 允许运行时修改的配置项
    allowed_keys = [
        # JWT/安全配置
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        # 冷存储配置
        "COLD_STORAGE_ENABLED",
        "COLD_STORAGE_TRIGGER_DAYS",
        "COLD_STORAGE_TRIGGER_VIEWS",
        "COLD_STORAGE_PATH_ROOT",
        # 存储迁移
        "STORAGE_MIGRATION_DELAY",
        # 日志配置
        "LOG_LEVEL",
    ]

    for key in updates.keys():
        if key not in allowed_keys:
            raise HTTPException(status_code=400, detail=f"Key {key} cannot be modified via API")

    # 更新数据库配置
    for key, value in updates.items():
        conf = session.exec(select(SystemConfig).where(SystemConfig.key == key)).first()
        if conf:
            conf.value = str(value)
            session.add(conf)
        else:
            conf = SystemConfig(key=key, value=str(value))
            session.add(conf)

    log_admin_action(session, admin.id, "update_env_config", None, f"Updated keys: {list(updates.keys())}")
    session.commit()

    return {"message": "Config updated successfully"}


# ==================== 存储管理 ====================

@router.get("/storage/config")
async def get_storage_config(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """获取存储配置"""
    configs = session.exec(select(SystemConfig)).all()
    result = {}
    for c in configs:
        if c.key.startswith("storage_"):
            # 去掉 storage_ 前缀返回
            result[c.key.replace("storage_", "")] = c.value

    result["STORAGE_MIGRATION_DELAY"] = settings.STORAGE_MIGRATION_DELAY
    result["COLD_STORAGE_ENABLED"] = settings.COLD_STORAGE_ENABLED
    result["COLD_STORAGE_PATH"] = str(settings.COLD_STORAGE_PATH)

    return result


@router.put("/storage/config")
async def update_storage_config(
    config: dict = Body(...),
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """更新存储配置"""
    allowed_keys = ["storage_migration_delay"]
    for key in config.keys():
        if key not in allowed_keys:
            raise HTTPException(status_code=400, detail=f"Key {key} cannot be modified")

    for key, value in config.items():
        conf = session.get(SystemConfig, key)
        if conf:
            conf.value = str(value)
            session.add(conf)
        else:
            conf = SystemConfig(key=key, value=str(value))
            session.add(conf)

        log_admin_action(session, admin.id, "update_storage_config", key, f"Set to {value}")

    session.commit()
    return {"message": "Storage config updated"}


@router.get("/menu-order")
async def get_menu_order(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """获取管理后台菜单顺序"""
    conf = session.get(SystemConfig, "admin_menu_order")
    if conf:
        import json
        try:
            return json.loads(conf.value)
        except Exception:
            pass
    return {}


@router.put("/menu-order")
async def update_menu_order(
    order: dict = Body(...),
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """更新管理后台菜单顺序"""
    import json
    conf = session.get(SystemConfig, "admin_menu_order")
    if conf:
        conf.value = json.dumps(order)
        session.add(conf)
    else:
        conf = SystemConfig(key="admin_menu_order", value=json.dumps(order))
        session.add(conf)

    log_admin_action(session, admin.id, "update_menu_order", None, "Menu order updated")
    session.commit()

    return {"message": "Menu order updated"}


@router.get("/storage/usage")
async def get_storage_usage(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """获取存储使用统计"""
    def get_dir_size(path):
        if not path.exists():
            return 0
        total = 0
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
        return total

    uploads_size = get_dir_size(settings.UPLOADS_DIR)
    processed_size = get_dir_size(settings.PROCESSED_DIR)
    thumbnails_size = get_dir_size(settings.THUMBNAILS_DIR)
    avatars_size = get_dir_size(settings.AVATARS_DIR)

    video_count = session.exec(select(func.count()).select_from(Video)).one()
    cold_video_count = session.exec(
        select(func.count()).select_from(Video).where(Video.is_cold == True)
    ).one()

    return {
        "uploads": {"size": uploads_size, "path": str(settings.UPLOADS_DIR), "disk": {"percent": psutil.disk_usage('/').percent}},
        "processed": {"size": processed_size, "path": str(settings.PROCESSED_DIR), "disk": {"percent": psutil.disk_usage('/').percent}},
        "thumbnails": {"size": thumbnails_size, "path": str(settings.THUMBNAILS_DIR), "disk": {"percent": psutil.disk_usage('/').percent}},
        "avatars": {"size": avatars_size, "path": str(settings.AVATARS_DIR), "disk": {"percent": psutil.disk_usage('/').percent}},
        "total_videos": video_count,
        "cold_videos": cold_video_count,
    }


@router.post("/storage/migrate")
async def start_storage_migration(
    old_dirs: dict = Body(...),
    new_dirs: dict = Body(...),
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """开始存储迁移任务"""
    task = migrate_storage_task.delay(old_dirs, new_dirs)
    log_admin_action(session, admin.id, "migrate_storage", None, f"Migrating from {old_dirs} to {new_dirs}")
    session.commit()

    return {"task_id": task.id, "message": "Migration started"}


@router.get("/storage/migrate/{task_id}")
async def get_migration_status(
    task_id: str,
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """获取迁移任务状态"""
    from tasks import celery_app
    result = celery_app.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "state": result.state,
        "result": result.result if result.ready() else None,
    }


@router.get("/storage/orphans")
async def get_orphan_files(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """获取孤立的文件（数据库中不存在但磁盘上有）"""
    from data_models import Video
    import os

    videos = session.exec(select(Video)).all()
    video_ids = set(str(v.id) for v in videos)

    orphans = []

    # 检查 uploads 目录
    if settings.UPLOADS_DIR.exists():
        for f in settings.UPLOADS_DIR.iterdir():
            if f.is_file():
                file_uuid = f.stem
                if file_uuid not in video_ids:
                    orphans.append({
                        "path": str(f),
                        "size": f.stat().st_size,
                        "reason": "uploads_not_in_db"
                    })

    return orphans


@router.post("/storage/cleanup")
async def cleanup_orphan_files(
    paths: List[str] = Body(...),
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """清理孤立文件"""
    deleted_count = 0
    freed_size = 0

    for path_str in paths:
        path = Path(path_str)
        if path.exists() and path.is_file():
            size = path.stat().st_size
            path.unlink()
            deleted_count += 1
            freed_size += size

    log_admin_action(session, admin.id, "cleanup_orphans", None, f"Deleted {deleted_count} files, freed {freed_size} bytes")
    session.commit()

    return {
        "deleted_count": deleted_count,
        "freed_size": freed_size
    }


@router.get("/logs")
async def get_admin_logs(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session),
    page: int = 1,
    size: int = 50
):
    """获取管理日志"""
    offset = (page - 1) * size
    logs = session.exec(
        select(AdminLog)
        .order_by(desc(AdminLog.created_at))
        .offset(offset)
        .limit(size)
    ).all()

    result = []
    for log in logs:
        log_dict = log.model_dump()
        admin_user = session.get(User, log.admin_id)
        log_dict["admin"] = {
            "id": str(admin_user.id),
            "username": admin_user.username,
            "display_name": admin_user.username,
        }
        result.append(log_dict)

    return result


# ==================== 转码队列 ====================

@router.get("/transcode/queue")
async def get_transcode_queue(
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """获取转码队列状态"""
    pending = session.exec(select(Video).where(Video.status == "pending").order_by(Video.created_at)).all()
    processing = session.exec(select(Video).where(Video.status == "processing").order_by(Video.created_at)).all()
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_completed = session.exec(
        select(Video).where(Video.status == "completed", Video.created_at >= today)
    ).all()
    failed = session.exec(
        select(Video).where(Video.status == "failed").order_by(desc(Video.created_at)).limit(20)
    ).all()

    result_pending = []
    for v in pending:
        video_dict = v.model_dump()
        owner = session.get(User, v.user_id)
        video_dict["owner"] = owner.username if owner else ""
        result_pending.append(video_dict)

    result_processing = []
    for v in processing:
        video_dict = v.model_dump()
        owner = session.get(User, v.user_id)
        video_dict["owner"] = owner.username if owner else ""
        result_processing.append(video_dict)

    result_completed = []
    for v in today_completed:
        video_dict = v.model_dump()
        owner = session.get(User, v.user_id)
        video_dict["owner"] = owner.username if owner else ""
        result_completed.append(video_dict)

    result_failed = []
    for v in failed:
        video_dict = v.model_dump()
        owner = session.get(User, v.user_id)
        video_dict["owner"] = owner.username if owner else ""
        result_failed.append(video_dict)

    return {
        "stats": {
            "pending_count": len(result_pending),
            "processing_count": len(result_processing),
            "completed_today": len(result_completed),
            "today_completed": len(result_completed),
            "failed_count": len(result_failed),
        },
        "pending": result_pending,
        "processing": result_processing,
        "completed_recent": result_completed,
        "failed": result_failed,
        "recently_failed": result_failed,
    }


@router.post("/transcode/{video_id}/retry")
async def retry_transcode(
    video_id: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """重试失败的转码"""
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video.status = "pending"
    video.progress = 0
    session.add(video)
    session.commit()

    transcode_video_task.delay(video_id)

    return {"message": "Transcode retry scheduled"}


# ==================== 冷存储 ====================

@router.get("/cold-storage/stats")
async def get_cold_storage_stats(
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """获取冷存储统计"""
    cold_count = session.exec(
        select(func.count()).select_from(Video).where(Video.is_cold == True)
    ).one()

    days_threshold = settings.COLD_STORAGE_TRIGGER_DAYS
    views_threshold = settings.COLD_STORAGE_TRIGGER_VIEWS
    cutoff_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    candidates_count = session.exec(
        select(func.count()).select_from(Video).where(
            Video.is_cold == False,
            Video.created_at < cutoff_date,
            Video.views < views_threshold,
            Video.status == "completed"
        )
    ).one()

    active_count = session.exec(
        select(func.count()).select_from(Video).where(Video.is_cold == False)
    ).one()

    cold_videos_query = session.exec(
        select(Video).where(Video.is_cold == True).order_by(desc(Video.created_at)).limit(20)
    ).all()

    cold_videos = []
    for v in cold_videos_query:
        video_dict = v.model_dump()
        owner = session.get(User, v.user_id)
        video_dict["owner"] = owner.username if owner else ""
        cold_videos.append(video_dict)

    return {
        "cold_count": cold_count,
        "candidates_count": candidates_count,
        "active_count": active_count,
        "threshold_days": days_threshold,
        "threshold_views": views_threshold,
        "cold_videos": cold_videos,
        "recent_cold_videos": cold_videos,
    }


@router.get("/cold-storage/candidates")
async def get_cold_storage_candidates(
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """获取符合条件的冷存储候选视频"""
    days_threshold = settings.COLD_STORAGE_TRIGGER_DAYS
    views_threshold = settings.COLD_STORAGE_TRIGGER_VIEWS
    cutoff_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    candidates = session.exec(
        select(Video).where(
            Video.is_cold == False,
            Video.created_at < cutoff_date,
            Video.views < views_threshold,
            Video.status == "completed"
        ).order_by(Video.created_at)
    ).all()

    result = []
    for v in candidates:
        video_dict = v.model_dump()
        owner = session.get(User, v.user_id)
        video_dict["owner"] = owner.username if owner else ""
        video_dict["age_days"] = (datetime.utcnow() - v.created_at).days
        result.append(video_dict)

    return {"count": len(result), "candidates": result}


@router.post("/cold-storage/migrate/{video_id}")
async def migrate_single_to_cold(
    video_id: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """将单个视频迁移到冷存储"""
    from tasks import cold_storage_migration_task

    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.is_cold:
        raise HTTPException(status_code=400, detail="Video already in cold storage")

    # 直接执行迁移（不通过 Celery）
    original_path = settings.fs_path(video.original_file_path)
    if original_path.exists():
        cold_upload_dir = settings.COLD_STORAGE_UPLOADS_DIR
        cold_upload_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        cold_file_path = cold_upload_dir / original_path.name
        shutil.copy2(original_path, cold_file_path)
        original_path.unlink()

    video.is_cold = True
    video.cold_stored_at = datetime.utcnow()
    session.add(video)
    session.commit()

    log_admin_action(session, admin.id, "migrate_to_cold", video_id, "Manual cold storage migration")
    session.commit()

    return {"message": "Video migrated to cold storage"}


@router.post("/cold-storage/restore/{video_id}")
async def restore_from_cold(
    video_id: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """从冷存储恢复视频"""
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if not video.is_cold:
        raise HTTPException(status_code=400, detail="Video not in cold storage")

    # 简化：仅标记为已恢复
    # 实际恢复需要从冷存储复制回主存储
    video.is_cold = False
    video.cold_stored_at = None
    session.add(video)
    session.commit()

    log_admin_action(session, admin.id, "restore_from_cold", video_id, "Restored from cold storage")
    session.commit()

    return {"message": "Video restored from cold storage"}


@router.post("/cold-storage/migrate-all")
async def migrate_all_to_cold(
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """迁移所有符合条件的视频到冷存储"""
    days_threshold = settings.COLD_STORAGE_TRIGGER_DAYS
    views_threshold = settings.COLD_STORAGE_TRIGGER_VIEWS
    cutoff_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    candidates = session.exec(
        select(Video).where(
            Video.is_cold == False,
            Video.created_at < cutoff_date,
            Video.views < views_threshold,
            Video.status == "completed"
        )
    ).all()

    migrated = 0
    for video in candidates:
        try:
            original_path = settings.fs_path(video.original_file_path)
            if original_path.exists():
                cold_upload_dir = settings.COLD_STORAGE_UPLOADS_DIR
                cold_upload_dir.mkdir(parents=True, exist_ok=True)
                import shutil
                cold_file_path = cold_upload_dir / original_path.name
                shutil.copy2(original_path, cold_file_path)
                original_path.unlink()

            video.is_cold = True
            video.cold_stored_at = datetime.utcnow()
            session.add(video)
            session.commit()
            migrated += 1
        except Exception as e:
            session.rollback()
            logger.warning(f"Failed to migrate video {video.id}: {e}")

    log_admin_action(session, admin.id, "migrate_all_to_cold", None, f"Migrated {migrated} videos")
    session.commit()

    return {"migrated": migrated}


# ==================== 用户管理 ====================

@router.get("/users", response_model=List[UserRead])
async def get_all_users(
    admin: User = Depends(PermissionChecker("user:ban")),
    session: Session = Depends(get_session),
    page: int = 1,
    size: int = 50
):
    """获取所有用户"""
    offset = (page - 1) * size
    users = session.exec(
        select(User).order_by(desc(User.created_at)).offset(offset).limit(size)
    ).all()
    return users


@router.post("/users/{user_id}/status")
async def update_user_status(
    user_id: str,
    is_active: bool = Body(...),
    admin: User = Depends(PermissionChecker("user:ban")),
    session: Session = Depends(get_session)
):
    """更新用户状态"""
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = is_active
    session.add(user)
    session.commit()

    log_admin_action(session, admin.id, "update_user_status", user_id, f"Set active={is_active}")
    session.commit()

    return {"message": "User status updated"}


@router.put("/users/{user_id}/role")
async def update_user_role(
    user_id: str,
    role_id: str = Body(...),
    admin: User = Depends(PermissionChecker("admin:super")),
    session: Session = Depends(get_session)
):
    """更新用户角色"""
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    role = session.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    user.role_id = role_id
    session.add(user)
    session.commit()

    log_admin_action(session, admin.id, "update_user_role", user_id, f"Role set to {role.name}")
    session.commit()

    return {"message": "User role updated"}


# ==================== 视频管理 ====================

@router.get("/videos")
async def get_all_videos(
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session),
    page: int = 1,
    size: int = 20
):
    """获取所有视频（管理视图）"""
    offset = (page - 1) * size
    videos = session.exec(
        select(Video).order_by(desc(Video.created_at)).offset(offset).limit(size)
    ).all()

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


@router.post("/videos/{video_id}/ban")
async def ban_video(
    video_id: str,
    admin: User = Depends(PermissionChecker("video:ban")),
    session: Session = Depends(get_session)
):
    """封禁视频"""
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video.status = "banned"
    session.add(video)
    session.commit()

    log_admin_action(session, admin.id, "ban_video", video_id, "Video banned")
    session.commit()

    return {"message": "Video banned"}


@router.post("/videos/{video_id}/approve")
async def approve_video(
    video_id: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """审核通过视频"""
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video.status = "completed"
    session.add(video)

    # 记录审核日志
    audit_log = VideoAuditLog(
        video_id=video_id,
        action="approve",
        admin_id=admin.id,
        reason="Approved by admin"
    )
    session.add(audit_log)
    session.commit()

    log_admin_action(session, admin.id, "approve_video", video_id, "Video approved")
    session.commit()

    return {"message": "Video approved"}


@router.post("/videos/{video_id}/status")
async def update_video_status(
    video_id: str,
    status: str = Body(...),
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """更新视频状态"""
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    old_status = video.status
    video.status = status
    session.add(video)

    audit_log = VideoAuditLog(
        video_id=video_id,
        action="status_change",
        admin_id=admin.id,
        reason=f"Status changed from {old_status} to {status}"
    )
    session.add(audit_log)
    session.commit()

    log_admin_action(session, admin.id, "update_video_status", video_id, f"Status: {old_status} -> {status}")
    session.commit()

    return {"message": "Video status updated"}


@router.get("/comments")
async def get_all_comments(
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session),
    page: int = 1,
    size: int = 50
):
    """获取所有评论"""
    offset = (page - 1) * size
    comments = session.exec(
        select(Comment).order_by(desc(Comment.created_at)).offset(offset).limit(size)
    ).all()

    result = []
    for c in comments:
        comment_dict = c.model_dump()
        user = session.get(User, c.user_id)
        comment_dict["user"] = {
            "id": str(user.id),
            "username": user.username,
            "display_name": user.username,
        }
        result.append(comment_dict)

    return result


@router.delete("/comments/{comment_id}")
async def delete_comment_admin(
    comment_id: int,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """删除评论（管理员）"""
    comment = session.get(Comment, comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    comment.is_deleted = True
    session.add(comment)
    session.commit()

    log_admin_action(session, admin.id, "delete_comment", str(comment_id), "Deleted by admin")
    session.commit()

    return {"message": "Comment deleted"}
