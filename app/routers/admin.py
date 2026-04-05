"""
管理后台路由
"""
from typing import List, Optional
from pydantic import BaseModel
from uuid import uuid4
from datetime import datetime
import psutil
import os
import shutil

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlmodel import Session, select, desc, func

from database import get_session, engine
from data_models import (
    User, UserRead, Video, VideoRead, Role, SystemConfig, AdminLog, UserRole,
    VideoAuditLog, Comment, Notification, Category,
    VideoRecommendation, VideoRecommendationWithVideoRead,
    RecommendationSlot, RecommendationSlotRead, RecommendationLog, UserVideoScore,
    TranscodeTask, TranscodeTaskRead
)
from dependencies import get_current_user, PermissionChecker, log_admin_action
from tasks import transcode_video_task, migrate_storage_task, celery_app
from config import settings, get_transcode_config
import socketio_handler

router = APIRouter(prefix="/admin", tags=["管理后台"])


class BanVideoRequest(BaseModel):
    reason: Optional[str] = None


# ==================== 公开配置（无需管理员权限） ====================

@router.get("/upload-config")
async def get_upload_config(session: Session = Depends(get_session)):
    """
    获取上传相关配置（公开接口，无需管理员权限）
    仅返回：MAX_UPLOAD_SIZE_MB
    """
    # 从数据库配置中读取，如果没配置则使用默认值
    try:
        config = session.exec(select(SystemConfig).where(SystemConfig.key == "MAX_UPLOAD_SIZE_MB")).first()
        max_upload_mb = int(config.value) if config else 2048
    except Exception:
        max_upload_mb = 2048

    # 支持的视频格式
    allowed_extensions = [".mp4", ".mpeg", ".mpg", ".mov", ".avi", ".wmv", ".webm", ".mkv", ".3gp", ".flv", ".m4v", ".ogv"]
    allowed_names = ["MP4", "MOV", "AVI", "WMV", "WebM", "MKV", "MPEG", "3GP", "FLV", "M4V", "OGV"]

    return {
        "max_upload_size_mb": max_upload_mb,
        "allowed_extensions": allowed_extensions,
        "allowed_names": allowed_names
    }


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
            "LOG_LEVEL": get_config_override("LOG_LEVEL", settings.LOG_LEVEL, session),
            "LOG_FILE": str(settings.LOG_FILE) if settings.LOG_FILE else "",
        },
        "敏感词": {
            "SENSITIVE_WORDS_FILE": str(settings.SENSITIVE_WORDS_PATH) if settings.SENSITIVE_WORDS_FILE else "",
        },
        "冷存储": {
            "COLD_STORAGE_ENABLED": get_config_override("COLD_STORAGE_ENABLED", settings.COLD_STORAGE_ENABLED, session),
            "COLD_STORAGE_TRIGGER_DAYS": get_config_override("COLD_STORAGE_TRIGGER_DAYS", settings.COLD_STORAGE_TRIGGER_DAYS, session),
            "COLD_STORAGE_TRIGGER_VIEWS": get_config_override("COLD_STORAGE_TRIGGER_VIEWS", settings.COLD_STORAGE_TRIGGER_VIEWS, session),
            "COLD_STORAGE_PATH_ROOT": get_config_override("COLD_STORAGE_PATH_ROOT", str(settings.COLD_STORAGE_PATH), session),
        },
        "存储迁移": {
            "STORAGE_MIGRATION_DELAY": get_config_override("STORAGE_MIGRATION_DELAY", settings.STORAGE_MIGRATION_DELAY, session),
        },
        "上传限制": {
            "MAX_UPLOAD_SIZE_MB": get_config_override("MAX_UPLOAD_SIZE_MB", settings.MAX_UPLOAD_SIZE_MB, session),
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
        # 上传限制
        "MAX_UPLOAD_SIZE_MB",
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
    session: Session = Depends(get_session),
    search: Optional[str] = None,
    status: Optional[str] = None,
    priority_type: Optional[str] = None
):
    """
    获取转码队列状态
    - search: 按视频标题或用户名搜索
    - status: 筛选状态 (pending, processing, completed, failed)
    - priority_type: 筛选优先级类型 (normal, vip, vip_speedup, paid_speedup)
    """
    # 构建基础查询
    query = select(TranscodeTask)
    if status:
        query = query.where(TranscodeTask.status == status)
    if priority_type:
        query = query.where(TranscodeTask.priority_type == priority_type)

    # 执行查询
    tasks = session.exec(query.order_by(desc(TranscodeTask.priority), TranscodeTask.created_at)).all()

    # 获取视频和用户信息
    result = []
    for task in tasks:
        video = session.get(Video, task.video_id)
        user = session.get(User, task.user_id)

        # 搜索过滤
        if search:
            video_title = video.title if video else ""
            username = user.username if user else ""
            if search.lower() not in video_title.lower() and search.lower() not in username.lower():
                continue

        task_dict = {
            "id": str(task.id),
            "video_id": str(task.video_id),
            "user_id": str(task.user_id),
            "status": task.status,
            "priority": task.priority,
            "priority_type": task.priority_type,
            "queue_name": task.queue_name,
            "worker_name": task.worker_name,
            "bump_count": task.bump_count,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "waiting_hours": task.waiting_hours,
            "video_title": video.title if video else "",
            "video_progress": video.progress if video else 0,
            "username": user.username if user else "",
            "is_vip": user.is_vip if user else False,
        }
        result.append(task_dict)

    # 按状态分组
    pending = [t for t in result if t["status"] == "pending"]
    processing = [t for t in result if t["status"] == "processing"]
    completed = [t for t in result if t["status"] == "completed"]
    failed = [t for t in result if t["status"] == "failed"]

    return {
        "stats": {
            "pending_count": len(pending),
            "processing_count": len(processing),
            "completed_today": len([t for t in completed if t["completed_at"] and datetime.fromisoformat(t["completed_at"]).date() == datetime.utcnow().date()]),
            "failed_count": len(failed),
            "total_count": len(result),
        },
        "tasks": result,
        "pending": pending,
        "processing": processing,
        "completed_recent": completed[:20],
        "failed": failed[:20],
    }


@router.post("/transcode/{task_id}/bump")
async def bump_transcode_task(
    task_id: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """
    管理员将指定任务插队到最前面（最高优先级，不扣积分）
    """
    task = session.get(TranscodeTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status != "pending":
        raise HTTPException(status_code=400, detail="Only pending tasks can be bumped")

    # 设置为最高优先级
    config = get_transcode_config()
    max_priority = config["max_priority"]

    task.priority = max_priority
    task.queue_name = "priority"
    task.worker_name = None  # 清除worker记录
    task.bump_count = (task.bump_count or 0) + 1

    # 如果是付费/VIP加速，保持类型；否则改为 vip_speedup
    if task.priority_type not in ("vip_speedup", "paid_speedup"):
        task.priority_type = "vip_speedup"

    session.add(task)
    session.commit()

    return {"message": "Task bumped to priority queue", "priority": task.priority, "bump_count": task.bump_count}


@router.post("/transcode/{task_id}/cancel")
async def cancel_transcode_task(
    task_id: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """
    取消转码任务（同时清理缓存文件）
    """
    task = session.get(TranscodeTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status in ("completed", "cancelled"):
        raise HTTPException(status_code=400, detail="Task cannot be cancelled")

    # 终止 Celery 任务
    if task.celery_task_id and task.status == "processing":
        try:
            celery_app.control.revoke(task.celery_task_id, terminate=True)
        except Exception:
            pass

    # 清理 Redis 锁
    try:
        from tasks import get_redis_client
        redis_client = get_redis_client()
        redis_client.delete(f"transcode_lock:{task.video_id}")
    except Exception:
        pass

    # 清理缓存文件
    processed_dir = settings.PROCESSED_DIR / str(task.video_id)
    if processed_dir.exists():
        shutil.rmtree(processed_dir)

    task.status = "cancelled"
    task.pause_percent = 0
    task.pause_resolution = None
    task.pause_timestamp = None
    session.add(task)

    # 如果视频正在转码，标记为 failed
    video = session.get(Video, task.video_id)
    if video and video.status == "processing":
        video.status = "failed"
        video.progress = 0
        session.add(video)

    session.commit()

    return {"message": "Task cancelled"}


@router.post("/transcode/{task_id}/pause")
async def pause_transcode_task(
    task_id: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """
    暂停转码任务（保存当前进度）
    """
    task = session.get(TranscodeTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status != "processing":
        raise HTTPException(status_code=400, detail="Only processing tasks can be paused")

    # 终止 Celery 任务
    if task.celery_task_id:
        try:
            celery_app.control.revoke(task.celery_task_id, terminate=True)
        except Exception:
            pass

    # 获取当前进度（从Redis或视频表）
    video = session.get(Video, task.video_id)
    current_progress = video.progress if video else 0

    # 保存暂停进度
    task.status = "paused"
    task.pause_percent = current_progress
    # 从Redis获取当前处理的分辨率和时间戳
    try:
        from tasks import get_redis_client
        redis_client = get_redis_client()
        task.pause_resolution = redis_client.get(f"transcode_resolution:{task.video_id}") or None
        task.pause_timestamp = redis_client.get(f"transcode_timestamp:{task.video_id}") or None
    except Exception:
        pass

    session.add(task)
    session.commit()

    return {"message": "Task paused", "pause_percent": task.pause_percent}


@router.post("/transcode/{task_id}/resume")
async def resume_transcode_task(
    task_id: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """
    继续转码任务（从暂停点恢复）
    """
    task = session.get(TranscodeTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status != "paused":
        raise HTTPException(status_code=400, detail="Only paused tasks can be resumed")

    # 保存恢复点信息到Redis，供任务读取
    try:
        from tasks import get_redis_client
        redis_client = get_redis_client()
        if task.pause_percent:
            redis_client.set(f"transcode_resume_percent:{task.video_id}", task.pause_percent, ex=3600)
        if task.pause_resolution:
            redis_client.set(f"transcode_resume_resolution:{task.video_id}", task.pause_resolution, ex=3600)
        if task.pause_timestamp:
            redis_client.set(f"transcode_resume_timestamp:{task.video_id}", task.pause_timestamp, ex=3600)
    except Exception:
        pass

    # 重置任务状态
    task.status = "pending"
    task.celery_task_id = None  # 清除旧的task_id
    session.add(task)

    # 视频状态重置为pending
    video = session.get(Video, task.video_id)
    if video:
        video.status = "pending"
        session.add(video)

    session.commit()

    # 重新提交Celery任务，传递恢复参数
    transcode_video_task.delay(str(task.video_id), "resume")

    return {"message": "Task resumed", "resume_from_percent": task.pause_percent}


@router.get("/transcode/config")
async def get_transcode_settings(
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """
    获取转码队列配置
    """
    config = get_transcode_config()
    return config


@router.put("/transcode/concurrency")
async def update_transcode_concurrency(
    concurrency: int = Body(..., ge=1, le=32),
    admin: User = Depends(PermissionChecker("admin:super")),
    session: Session = Depends(get_session)
):
    """
    更新转码并发数配置
    """
    # 保存到系统配置
    config = session.exec(select(SystemConfig).where(SystemConfig.key == "TRANSCODE_CONCURRENCY")).first()
    if config:
        config.value = str(concurrency)
        session.add(config)
    else:
        config = SystemConfig(
            key="TRANSCODE_CONCURRENCY",
            value=str(concurrency),
            value_type="int",
            description="转码并发数"
        )
        session.add(config)

    session.commit()

    return {"message": "Concurrency updated", "concurrency": concurrency}


@router.post("/transcode/{video_id}/retry")
async def retry_transcode(
    video_id: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """重试失败的转码（仅限1次）"""
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # 查找失败的任务记录
    task = session.exec(
        select(TranscodeTask).where(
            TranscodeTask.video_id == video.id,
            TranscodeTask.status == "failed"
        )
    ).first()

    if task:
        # 管理员重试不限制次数
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

    # 获取每个用户的角色信息
    result = []
    for user in users:
        user_roles = session.exec(select(UserRole).where(UserRole.user_id == user.id)).all()
        role_ids = [ur.role_id for ur in user_roles]
        role_names = []
        for rid in role_ids:
            role = session.get(Role, rid)
            if role:
                role_names.append(role.name)
        result.append(UserRead(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_admin=user.is_admin,
            role_ids=role_ids,
            role_names=role_names,
            created_at=user.created_at,
            avatar_path=user.avatar_path,
            bio=user.bio
        ))
    return result


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
    body: dict = Body(...),
    admin: User = Depends(PermissionChecker("admin:super")),
    session: Session = Depends(get_session)
):
    """更新用户角色（支持多角色）"""
    role_ids = body.get("role_ids")
    if role_ids is None:
        raise HTTPException(status_code=400, detail="role_ids is required")

    if not isinstance(role_ids, list):
        raise HTTPException(status_code=400, detail="role_ids must be a list")

    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 验证所有角色存在
    for rid in role_ids:
        role = session.get(Role, rid)
        if not role:
            raise HTTPException(status_code=404, detail=f"Role {rid} not found")

    # 删除旧的角色关联
    old_user_roles = session.exec(select(UserRole).where(UserRole.user_id == user.id)).all()
    for ur in old_user_roles:
        session.delete(ur)

    # 添加新的角色关联
    role_names = []
    for rid in role_ids:
        ur = UserRole(user_id=user.id, role_id=rid)
        session.add(ur)
        role = session.get(Role, rid)
        role_names.append(role.name)

    log_admin_action(session, admin.id, "update_user_role", user_id, f"Roles set to {', '.join(role_names)}")
    session.commit()

    return {"message": "User roles updated", "role_names": role_names}


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

    # 手动添加 owner 和 category 信息
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
            }
        result.append(video_dict)

    return result


@router.post("/videos/{video_id}/ban")
async def ban_video(
    video_id: str,
    request: BanVideoRequest = None,
    admin: User = Depends(PermissionChecker("video:ban")),
    session: Session = Depends(get_session)
):
    """封禁视频"""
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video.status = "banned"
    session.add(video)

    # 记录审核日志
    audit_log = VideoAuditLog(
        video_id=video_id,
        operator_id=admin.id,
        action="ban",
        reason=request.reason or "Video banned"
    )
    session.add(audit_log)

    # 发送通知给视频所有者
    if video.user_id:
        notif = Notification(
            sender_id=admin.id,
            recipient_id=video.user_id,
            type="system",
            entity_id=str(video.id),
            content=f"您的视频《{video.title}》因「{request.reason or '违规内容'}」已被下架"
        )
        session.add(notif)

    session.commit()

    # 发送 WebSocket 通知
    if video.user_id:
        unread_count = session.exec(
            select(Notification).where(
                Notification.recipient_id == str(video.user_id),
                Notification.is_read == False
            )
        ).all()
        socketio_handler.publish_notification_count(str(video.user_id), len(unread_count), settings.REDIS_URL)

    log_admin_action(session, admin.id, "ban_video", video_id, f"Video banned: {request.reason}")

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

    video.status = "approved"
    session.add(video)

    # 记录审核日志
    audit_log = VideoAuditLog(
        video_id=video_id,
        operator_id=admin.id,
        action="approve",
        reason="Approved by admin"
    )
    session.add(audit_log)

    # 发送通知给视频所有者
    if video.user_id:
        notif = Notification(
            sender_id=admin.id,
            recipient_id=video.user_id,
            type="system",
            entity_id=str(video.id),
            content=f"您的视频《{video.title}》已通过审核并上架"
        )
        session.add(notif)

    session.commit()

    # 发送 WebSocket 通知
    if video.user_id:
        unread_count = session.exec(
            select(Notification).where(
                Notification.recipient_id == str(video.user_id),
                Notification.is_read == False
            )
        ).all()
        socketio_handler.publish_notification_count(str(video.user_id), len(unread_count), settings.REDIS_URL)

    log_admin_action(session, admin.id, "approve_video", video_id, "Video approved")

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
        operator_id=admin.id,
        action="status_change",
        reason=f"Status changed from {old_status} to {status}"
    )
    session.add(audit_log)

    # 发送通知给视频所有者
    if video.user_id:
        status_msg = {
            "approved": "已通过审核并上架",
            "banned": "已被下架",
            "appealing": "申诉中",
            "pending": "待审核",
            "processing": "处理中"
        }.get(status, f"状态变更为 {status}")

        notif = Notification(
            sender_id=admin.id,
            recipient_id=video.user_id,
            type="system",
            entity_id=str(video.id),
            content=f"您的视频《{video.title}》{status_msg}"
        )
        session.add(notif)

    session.commit()

    # 发送 WebSocket 通知
    if video.user_id:
        unread_count = session.exec(
            select(Notification).where(
                Notification.recipient_id == str(video.user_id),
                Notification.is_read == False
            )
        ).all()
        socketio_handler.publish_notification_count(str(video.user_id), len(unread_count), settings.REDIS_URL)

    log_admin_action(session, admin.id, "update_video_status", video_id, f"Status: {old_status} -> {status}")

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
