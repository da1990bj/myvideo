"""
管理后台路由
"""
from pathlib import Path
from typing import Any, List, Optional
from pydantic import BaseModel
from uuid import uuid4, UUID
from datetime import datetime, timedelta
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
    TranscodeTask, TranscodeTaskRead,
    VideoLike, VideoFavorite, CollectionItem, VideoTag,
    UserVideoHistory, AnonymousViewHistory
)
from dependencies import get_current_user, PermissionChecker, log_admin_action
from tasks import transcode_video_task, migrate_storage_task, celery_app
from config import settings, get_transcode_config, get_runtime_config, reload_runtime_config
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
        "network": {
            "bytes_sent": psutil.net_io_counters().bytes_sent,
            "bytes_recv": psutil.net_io_counters().bytes_recv,
            "packets_sent": psutil.net_io_counters().packets_sent,
            "packets_recv": psutil.net_io_counters().packets_recv,
        },
        "connections_count": len(psutil.net_connections()),
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
    all_videos = session.exec(select(func.count()).select_from(Video)).one()
    comments = session.exec(select(func.count()).select_from(Comment)).one()

    # 视频统计（不包含已删除）
    total_videos = session.exec(select(func.count()).select_from(Video).where(Video.is_deleted == False)).one()
    private_videos = session.exec(select(func.count()).select_from(Video).where(Video.is_deleted == False, Video.visibility == "private")).one()
    pending_videos = session.exec(select(func.count()).select_from(Video).where(Video.is_deleted == False, Video.is_approved == "pending")).one()
    approved_videos = session.exec(select(func.count()).select_from(Video).where(Video.is_deleted == False, Video.is_approved == "approved")).one()
    banned_videos = session.exec(select(func.count()).select_from(Video).where(Video.is_deleted == False, Video.is_approved == "banned")).one()

    pending = session.exec(select(Video).where(Video.status == "pending", Video.is_deleted == False).order_by(Video.created_at)).all()
    processing = session.exec(select(Video).where(Video.status == "processing", Video.is_deleted == False).order_by(Video.created_at)).all()
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_completed = session.exec(
        select(Video).where(Video.status == "completed", Video.is_deleted == False, Video.created_at >= today)
    ).all()
    failed = session.exec(
        select(Video).where(Video.status == "failed", Video.is_deleted == False).order_by(desc(Video.created_at)).limit(20)
    ).all()

    return {
        "users": users,
        "total_videos": total_videos,
        "all_videos": all_videos,
        "private_videos": private_videos,
        "pending_videos_count": pending_videos,
        "approved_videos_count": approved_videos,
        "banned_videos_count": banned_videos,
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
    """获取环境配置（敏感）

    注：必填配置（MYVIDEO_ROOT, DATABASE_*, REDIS_*, SECRET_KEY）不在此处显示，
    如需修改请直接编辑 .env 文件。
    """
    # 配置分组定义（仅选填/可运行时修改的配置）
    GROUPS = {
        "站点信息": ["SITE_NAME", "SHARE_BASE_URL"],
        "应用服务器": ["APP_HOST", "APP_PORT", "APP_DEBUG"],
        "安全/JWT": ["ALGORITHM", "ACCESS_TOKEN_EXPIRE_MINUTES"],
        "文件存储目录": ["STATIC_SUBDIR", "UPLOADS_SUBDIR", "PROCESSED_SUBDIR", "THUMBNAILS_SUBDIR", "AVATARS_SUBDIR", "DATA_SUBDIR"],
        "URL路径前缀": ["THUMBNAILS_URL", "VIDEOS_URL", "AVATARS_URL"],
        "CORS": ["CORS_ORIGINS", "CORS_CREDENTIALS", "CORS_METHODS", "CORS_HEADERS"],
        "Celery": ["CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"],
        "日志": ["LOG_LEVEL", "LOG_FILE"],
        "敏感词": ["SENSITIVE_WORDS_FILE"],
        "冷存储": ["COLD_STORAGE_ENABLED", "COLD_STORAGE_TRIGGER_DAYS", "COLD_STORAGE_TRIGGER_VIEWS", "COLD_STORAGE_PATH_ROOT"],
    }

    # 构建配置数据
    config_data = {
        "站点信息": {
            "SITE_NAME": get_runtime_config("SITE_NAME", settings.SITE_NAME),
            "SHARE_BASE_URL": get_runtime_config("SHARE_BASE_URL", settings.SHARE_BASE_URL) or "",
        },
        "应用服务器": {
            "APP_HOST": settings.APP_HOST,
            "APP_PORT": settings.APP_PORT,
            "APP_DEBUG": settings.APP_DEBUG,
        },
        "安全/JWT": {
            "ALGORITHM": settings.ALGORITHM,
            "ACCESS_TOKEN_EXPIRE_MINUTES": get_runtime_config("ACCESS_TOKEN_EXPIRE_MINUTES", settings.ACCESS_TOKEN_EXPIRE_MINUTES),
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
            "LOG_LEVEL": get_runtime_config("LOG_LEVEL", settings.LOG_LEVEL),
            "LOG_FILE": str(settings.LOG_FILE) if settings.LOG_FILE else "",
        },
        "敏感词": {
            "SENSITIVE_WORDS_FILE": str(settings.SENSITIVE_WORDS_PATH) if settings.SENSITIVE_WORDS_FILE else "",
        },
        "冷存储": {
            "COLD_STORAGE_ENABLED": get_runtime_config("COLD_STORAGE_ENABLED", settings.COLD_STORAGE_ENABLED),
            "COLD_STORAGE_TRIGGER_DAYS": get_runtime_config("COLD_STORAGE_TRIGGER_DAYS", settings.COLD_STORAGE_TRIGGER_DAYS),
            "COLD_STORAGE_TRIGGER_VIEWS": get_runtime_config("COLD_STORAGE_TRIGGER_VIEWS", settings.COLD_STORAGE_TRIGGER_VIEWS),
            "COLD_STORAGE_PATH_ROOT": get_runtime_config("COLD_STORAGE_PATH_ROOT", str(settings.COLD_STORAGE_PATH)),
        },
    }

    # 处理排除
    if exclude:
        exclude_list = [s.strip() for s in exclude.split(",")]
        for section in exclude_list:
            if section in config_data:
                del config_data[section]

    return {"config": config_data}


@router.put("/env-config")
async def update_env_config(
    updates: dict = Body(...),
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """更新环境配置（支持运行时修改的配置项）"""
    # 允许运行时修改的配置项（必填配置如 DATABASE_* 等不在此处，如需修改请编辑 .env）
    allowed_keys = [
        # 站点信息
        "SITE_NAME",
        # 安全/JWT
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        # 分享设置
        "SHARE_BASE_URL",
        # 日志配置
        "LOG_LEVEL",
        # 冷存储配置
        "COLD_STORAGE_ENABLED",
        "COLD_STORAGE_TRIGGER_DAYS",
        "COLD_STORAGE_TRIGGER_VIEWS",
        "COLD_STORAGE_PATH_ROOT",
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
    reload_runtime_config()

    return {"message": "Config updated successfully"}


@router.post("/reload")
async def reload_server(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """热更新运行时配置（不重启服务）"""
    try:
        reload_runtime_config()
        log_admin_action(session, admin.id, "reload_config", None, "Runtime config hot-reloaded")
        session.commit()
        return {"message": "配置已热更新，所有运行时配置将使用新值"}
    except Exception as e:
        return {"message": f"热更新失败: {str(e)}"}


# ==================== 存储管理 ====================

@router.get("/storage/config")
async def get_storage_config(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """获取存储配置"""
    # 从 settings 计算实际路径
    result = {
        "uploads_dir": str(settings.UPLOADS_DIR),
        "processed_dir": str(settings.PROCESSED_DIR),
        "thumbnails_dir": str(settings.THUMBNAILS_DIR),
    }

    # 数据库中额外配置
    configs = session.exec(select(SystemConfig)).all()
    for c in configs:
        if c.key.startswith("storage_"):
            result[c.key.replace("storage_", "")] = c.value

    result["STORAGE_MIGRATION_DELAY"] = get_runtime_config("STORAGE_MIGRATION_DELAY", 0.5)
    result["STORAGE_BACKEND"] = get_runtime_config("STORAGE_BACKEND", settings.STORAGE_BACKEND)
    result["MAX_UPLOAD_SIZE_MB"] = get_runtime_config("MAX_UPLOAD_SIZE_MB", settings.MAX_UPLOAD_SIZE_MB)
    result["COLD_STORAGE_ENABLED"] = get_runtime_config("COLD_STORAGE_ENABLED", False)
    result["COLD_STORAGE_PATH"] = get_runtime_config("COLD_STORAGE_PATH_ROOT", "/data/myvideo/cold_storage")

    return result


@router.put("/storage/config")
async def update_storage_config(
    config: dict = Body(...),
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """更新存储配置"""
    import json

    allowed_keys = [
        "STORAGE_MIGRATION_DELAY",
        "STORAGE_BACKEND",
        "MAX_UPLOAD_SIZE_MB",
        "UPLOADS_SUBDIR",
        "PROCESSED_SUBDIR",
        "THUMBNAILS_SUBDIR",
    ]

    for key in config.keys():
        if key not in allowed_keys:
            raise HTTPException(status_code=400, detail=f"Key {key} cannot be modified")

    for key, value in config.items():
        # 如果是目录配置变更，记录旧值到历史
        if key in ("UPLOADS_SUBDIR", "PROCESSED_SUBDIR", "THUMBNAILS_SUBDIR"):
            old_value = get_runtime_config(key)
            if old_value and old_value != value:
                # 追加到历史
                history_key = key + "_HISTORY"
                history_value = get_runtime_config(history_key, "[]")
                try:
                    history_list = json.loads(history_value)
                except Exception:
                    history_list = []
                if old_value not in history_list:
                    history_list.append(old_value)
                # 保存历史
                conf = session.exec(select(SystemConfig).where(SystemConfig.key == history_key)).first()
                if conf:
                    conf.value = json.dumps(history_list)
                    session.add(conf)
                else:
                    session.add(SystemConfig(key=history_key, value=json.dumps(history_list)))

        conf = session.get(SystemConfig, key)
        if conf:
            conf.value = str(value)
            session.add(conf)
        else:
            conf = SystemConfig(key=key, value=str(value))
            session.add(conf)

        log_admin_action(session, admin.id, "update_storage_config", key, f"Set to {value}")

    session.commit()
    reload_runtime_config()
    return {"message": "Storage config updated"}


@router.get("/storage/directories")
async def get_storage_directories(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """获取存储目录信息（当前目录 + 历史目录）"""
    import json

    # 当前目录
    current = {
        "uploads": {
            "path": str(settings.UPLOADS_DIR),
            "subdir": get_runtime_config("UPLOADS_SUBDIR", settings.UPLOADS_SUBDIR),
            "is_current": True,
        },
        "processed": {
            "path": str(settings.PROCESSED_DIR),
            "subdir": get_runtime_config("PROCESSED_SUBDIR", settings.PROCESSED_SUBDIR),
            "is_current": True,
        },
        "thumbnails": {
            "path": str(settings.THUMBNAILS_DIR),
            "subdir": get_runtime_config("THUMBNAILS_SUBDIR", settings.THUMBNAILS_SUBDIR),
            "is_current": True,
        },
    }

    # 历史目录
    history = []
    for key in ("uploads", "processed", "thumbnails"):
        history_key = f"{key.upper()}_HISTORY"
        history_value = get_runtime_config(history_key, "[]")
        try:
            history_list = json.loads(history_value)
        except Exception:
            history_list = []

        for subdir in history_list:
            dir_path = settings.BASE_DIR / subdir
            file_count = 0
            total_size = 0
            if dir_path.exists():
                for f in dir_path.rglob("*"):
                    if f.is_file():
                        file_count += 1
                        try:
                            total_size += f.stat().st_size
                        except Exception:
                            pass

            history.append({
                "type": key,
                "path": str(dir_path),
                "subdir": subdir,
                "file_count": file_count,
                "size": total_size,
                "size_display": _format_size(total_size),
                "is_current": False,
            })

    return {"current": current, "history": history}


def _format_size(size: int) -> str:
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PB"


@router.post("/storage/migrate/{dir_type}")
async def migrate_storage_directory(
    dir_type: str,
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session),
    concurrency: int = Query(2, ge=1, le=10, description="并发数"),
    max_speed: float = Query(0, ge=0, description="最大速度MB/s，0表示不限制")
):
    """启动异步迁移任务"""
    import json

    if dir_type not in ("uploads", "processed", "thumbnails"):
        raise HTTPException(status_code=400, detail="Invalid directory type")

    history_key = f"{dir_type.upper()}_HISTORY"
    history_value = get_runtime_config(history_key, "[]")
    try:
        history_list = json.loads(history_value)
    except Exception:
        history_list = []

    if not history_list:
        raise HTTPException(status_code=400, detail="No history directory to migrate from")

    # 使用最近的历史目录
    source_subdir = history_list[-1]

    # 启动异步任务
    from tasks import migrate_storage_task
    task = migrate_storage_task.delay(dir_type, source_subdir, admin.id, concurrency, max_speed)

    log_admin_action(session, admin.id, "start_migrate_storage", dir_type,
                    f"Started migration from {source_subdir}, concurrency={concurrency}, max_speed={max_speed}MB/s")
    session.commit()

    return {"task_id": task.id, "message": f"迁移任务已启动 (并发:{conciversity}, 限速:{max_speed}MB/s)"}


@router.get("/storage/migrate/status/{task_id}")
async def get_migration_status(
    task_id: str,
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """获取迁移任务进度"""
    from tasks import migrate_storage_task

    task = migrate_storage_task.AsyncResult(task_id)

    if task.state == 'PENDING':
        return {"state": "PENDING", "message": "Task is pending..."}
    elif task.state == 'PROGRESS':
        return {
            "state": "PROGRESS",
            "current": task.info.get('current', 0),
            "total": task.info.get('total', 0),
            "migrated": task.info.get('migrated', 0),
            "failed": task.info.get('failed', 0),
            "current_file": task.info.get('current_file', ''),
            "message": task.info.get('status', 'Migrating...')
        }
    elif task.state == 'SUCCESS':
        return {
            "state": "SUCCESS",
            "result": task.result,
            "message": "Migration completed"
        }
    elif task.state == 'FAILURE':
        return {"state": "FAILURE", "message": str(task.info)}
    else:
        return {"state": task.state, "message": str(task.info)}


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


@router.get("/card-order")
async def get_card_order(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """获取管理后台卡片顺序"""
    import json
    orders = {}
    # 获取所有以 card_order_ 开头的配置
    configs = session.exec(select(SystemConfig).where(SystemConfig.key.like("card_order_%"))).all()
    for conf in configs:
        try:
            orders[conf.key.replace("card_order_", "")] = json.loads(conf.value)
        except:
            orders[conf.key.replace("card_order_", "")] = []
    return orders


@router.put("/card-order/{page}")
async def update_card_order(
    page: str,
    order: list = Body(...),
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """更新管理后台指定页面的卡片顺序"""
    import json
    key = f"card_order_{page}"
    conf = session.get(SystemConfig, key)
    if conf:
        conf.value = json.dumps(order)
    else:
        conf = SystemConfig(key=key, value=json.dumps(order))
        session.add(conf)

    session.commit()
    return {"message": "Card order updated", "page": page}


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

    orphan_files = {
        "uploads": [],
        "processed": [],
        "thumbnails": []
    }

    # 检查 uploads 目录
    if settings.UPLOADS_DIR.exists():
        for f in settings.UPLOADS_DIR.iterdir():
            if f.is_file():
                file_uuid = f.stem
                if file_uuid not in video_ids:
                    orphan_files["uploads"].append({
                        "path": str(f),
                        "size": f.stat().st_size,
                        "reason": "uploads_not_in_db"
                    })

    # 检查 processed 目录（孤立视频目录）
    if settings.PROCESSED_DIR.exists():
        for d in settings.PROCESSED_DIR.iterdir():
            if d.is_dir():
                dir_uuid = d.name
                if dir_uuid not in video_ids:
                    # 计算目录大小
                    total_size = sum(f.stat().st_size for f in d.rglob('*') if f.is_file())
                    orphan_files["processed"].append({
                        "path": str(d),
                        "size": total_size,
                        "reason": "processed_not_in_db"
                    })

    # 检查 thumbnails 目录
    if settings.THUMBNAILS_DIR.exists():
        for f in settings.THUMBNAILS_DIR.iterdir():
            if f.is_file():
                file_uuid = f.stem
                # 缩略图文件名格式: video_id_timestamp.jpg
                # 检查是否是某个视频的缩略图
                is_orphan = True
                for vid in video_ids:
                    if file_uuid.startswith(vid):
                        is_orphan = False
                        break
                if is_orphan:
                    orphan_files["thumbnails"].append({
                        "path": str(f),
                        "size": f.stat().st_size,
                        "reason": "thumbnail_not_in_db"
                    })

    # 计算总数和总大小
    total_count = len(orphan_files["uploads"]) + len(orphan_files["processed"]) + len(orphan_files["thumbnails"])
    total_size = sum(f["size"] for f in orphan_files["uploads"]) + \
                 sum(d["size"] for d in orphan_files["processed"]) + \
                 sum(f["size"] for f in orphan_files["thumbnails"])

    return {
        "total_count": total_count,
        "total_size": total_size,
        "files": orphan_files
    }


@router.post("/storage/cleanup")
async def cleanup_orphan_files(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session),
    request_data: Any = Body(...)
):
    """清理孤立文件"""
    import logging
    logger = logging.getLogger(__name__)

    # 支持两种格式: {"paths": [...]} 或直接是数组 [...]
    if isinstance(request_data, list):
        paths = request_data
    elif isinstance(request_data, dict):
        paths = request_data.get("paths", [])
    else:
        paths = []

    if not isinstance(paths, list):
        paths = []

    deleted_count = 0
    freed_size = 0
    errors = []

    for path_str in paths:
        try:
            p = Path(path_str)
            if not p.exists():
                errors.append(f"{path_str}: does not exist")
                continue
            if p.is_file():
                size = p.stat().st_size
                p.unlink()
                deleted_count += 1
                freed_size += size
            elif p.is_dir():
                import shutil
                size = sum(f.stat().st_size for f in p.rglob('*') if f.is_file())
                shutil.rmtree(p)
                deleted_count += 1
                freed_size += size
        except Exception as e:
            errors.append(f"{path_str}: {str(e)}")

    try:
        log_admin_action(session, admin.id, "cleanup_orphans", None, f"Deleted {deleted_count} files, freed {freed_size} bytes")
        session.commit()
    except Exception as e:
        session.rollback()
        errors.append(f"log_admin_action failed: {str(e)}")

    return {
        "deleted_count": deleted_count,
        "freed_size": freed_size,
        "errors": errors
    }


@router.post("/storage/full-cleanup")
async def full_storage_cleanup(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """
    统一存储清理：
    1. 清理已删除视频(status=deleted)的数据库关联记录
    2. 清理孤立文件（磁盘存在但数据库无记录）
    """
    import logging
    import shutil
    from pathlib import Path
    from config import settings
    from data_models import (
        Comment, VideoLike, VideoFavorite, VideoAuditLog,
        CollectionItem, VideoTag, RecommendationLog,
        UserVideoHistory, UserVideoScore, AnonymousViewHistory,
        TranscodeTask
    )

    logger = logging.getLogger(__name__)
    result = {
        "deleted_videos": 0,
        "deleted_records": 0,
        "orphan_files": 0,
        "freed_size": 0,
        "errors": []
    }

    # 1. 清理已删除视频(is_deleted=True)的数据库记录
    deleted_videos = session.exec(
        select(Video).where(Video.is_deleted == True)
    ).all()

    for video in deleted_videos:
        try:
            # 删除关联记录
            for model in [Comment, VideoLike, VideoFavorite, VideoAuditLog,
                         CollectionItem, VideoTag, RecommendationLog,
                         UserVideoHistory, UserVideoScore, AnonymousViewHistory,
                         TranscodeTask]:
                records = session.exec(
                    select(model).where(model.video_id == video.id)
                ).all()
                for r in records:
                    session.delete(r)
                result["deleted_records"] += len(records)

            session.delete(video)
            result["deleted_videos"] += 1
        except Exception as e:
            result["errors"].append(f"Video {video.id}: {str(e)}")

    # 2. 清理孤立文件
    video_ids_in_db = set(str(v.id) for v in session.exec(select(Video.id)).all())

    # 扫描 processed 目录
    processed_dir = settings.PROCESSED_DIR
    if processed_dir.exists():
        for video_dir in processed_dir.iterdir():
            if video_dir.is_dir() and video_dir.name not in video_ids_in_db:
                try:
                    size = sum(f.stat().st_size for f in video_dir.rglob('*') if f.is_file())
                    shutil.rmtree(video_dir)
                    result["orphan_files"] += 1
                    result["freed_size"] += size
                except Exception as e:
                    result["errors"].append(f"Orphan dir {video_dir.name}: {str(e)}")

    # 扫描 uploads 目录（按视频ID子目录）
    uploads_dir = settings.UPLOADS_DIR
    if uploads_dir.exists():
        for item in uploads_dir.iterdir():
            # 文件名可能是 {uuid}.mp4 或 {uuid} 目录
            name = item.stem if item.suffix else item.name
            try:
                uuid.UUID(name)  # 验证是否是UUID
                if name not in video_ids_in_db:
                    if item.is_file():
                        size = item.stat().st_size
                        item.unlink()
                        result["orphan_files"] += 1
                        result["freed_size"] += size
                    elif item.is_dir():
                        size = sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
                        shutil.rmtree(item)
                        result["orphan_files"] += 1
                        result["freed_size"] += size
            except (ValueError, OSError):
                pass  # 跳过非UUID命名的文件

    # 扫描 thumbnails 目录
    thumbnails_dir = settings.THUMBNAILS_DIR
    if thumbnails_dir.exists():
        for item in thumbnails_dir.iterdir():
            name = item.stem.split('_')[0]  # 可能是 {uuid}.jpg 或 {uuid}_timestamp.jpg
            try:
                uuid.UUID(name)
                if name not in video_ids_in_db:
                    size = item.stat().st_size
                    item.unlink()
                    result["orphan_files"] += 1
                    result["freed_size"] += size
            except (ValueError, OSError):
                pass

    try:
        log_admin_action(session, admin.id, "full_cleanup",
                        None, f"Deleted {result['deleted_videos']} videos, {result['deleted_records']} records, {result['orphan_files']} orphan files, freed {result['freed_size']} bytes")
        session.commit()
    except Exception as e:
        session.rollback()
        result["errors"].append(f"log_admin_action failed: {str(e)}")

    return result


@router.get("/storage/deleted-videos")
async def get_deleted_videos(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """获取已删除视频列表"""
    videos = session.exec(
        select(Video).where(Video.is_deleted == True).order_by(desc(Video.deleted_at))
    ).all()

    result = []
    for v in videos:
        # 计算关联记录数
        comment_count = session.exec(select(func.count(Comment.id)).where(Comment.video_id == v.id)).scalar()
        like_count = session.exec(select(func.count(VideoLike.id)).where(VideoLike.video_id == v.id)).scalar()
        fav_count = session.exec(select(func.count(VideoFavorite.id)).where(VideoFavorite.video_id == v.id)).scalar()

        result.append({
            "id": str(v.id),
            "title": v.title,
            "user_id": str(v.user_id),
            "deleted_at": v.deleted_at.isoformat() if v.deleted_at else None,
            "comment_count": comment_count or 0,
            "like_count": like_count or 0,
            "favorite_count": fav_count or 0
        })

    return result


@router.get("/storage/orphan-files")
async def get_orphan_files(
    admin: User = Depends(PermissionChecker("*")),
    session: Session = Depends(get_session)
):
    """获取孤立文件列表"""
    from config import settings

    video_ids_in_db = set(str(v.id) for v in session.exec(select(Video.id)).all())
    orphan_files = []

    # 扫描 processed 目录
    processed_dir = settings.PROCESSED_DIR
    if processed_dir.exists():
        for video_dir in processed_dir.iterdir():
            if video_dir.is_dir() and video_dir.name not in video_ids_in_db:
                size = sum(f.stat().st_size for f in video_dir.rglob('*') if f.is_file())
                orphan_files.append({
                    "path": str(video_dir),
                    "type": "directory",
                    "size": size
                })

    # 扫描 uploads 目录
    uploads_dir = settings.UPLOADS_DIR
    if uploads_dir.exists():
        for item in uploads_dir.iterdir():
            name = item.stem if item.suffix else item.name
            try:
                uuid.UUID(name)
                if name not in video_ids_in_db:
                    size = item.stat().st_size if item.is_file() else sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
                    orphan_files.append({
                        "path": str(item),
                        "type": "directory" if item.is_dir() else "file",
                        "size": size
                    })
            except (ValueError, OSError):
                pass

    # 扫描 thumbnails 目录
    thumbnails_dir = settings.THUMBNAILS_DIR
    if thumbnails_dir.exists():
        for item in thumbnails_dir.iterdir():
            name = item.stem.split('_')[0]
            try:
                uuid.UUID(name)
                if name not in video_ids_in_db:
                    orphan_files.append({
                        "path": str(item),
                        "type": "file",
                        "size": item.stat().st_size
                    })
            except (ValueError, OSError):
                pass

    total_size = sum(f["size"] for f in orphan_files)
    return {"files": orphan_files, "total_count": len(orphan_files), "total_size": total_size}


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

    # 清理 stale 任务：如果视频已完成/批准但任务仍为 processing，自动更新任务状态
    for task in tasks:
        if task.status == "processing" and task.completed_at is None:
            video = session.get(Video, task.video_id)
            if video and video.status in ("completed", "failed"):
                task.status = video.status
                task.completed_at = datetime.utcnow()
                session.add(task)
    if tasks:
        session.commit()

    # 获取视频和用户信息
    result = []
    for task in tasks:
        video = session.get(Video, task.video_id)
        user = session.get(User, task.user_id)

        # 跳过已删除的视频
        if not video or video.is_deleted:
            continue

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


@router.get("/transcode/scan-abnormal")
async def scan_abnormal_transcode_tasks(
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """
    扫描转码异常状态：
    1. Video.status='pending' 但没有对应的活跃 TranscodeTask
    2. Video.status='processing' 但没有对应的活跃 TranscodeTask 且超过30分钟
    """
    abnormal_list = []

    # 查找 Video.status='pending' 但没有活跃任务的视频
    pending_videos = session.exec(
        select(Video).where(Video.status == "pending", Video.is_deleted == False)
    ).all()

    for video in pending_videos:
        # 检查是否有活跃的转码任务
        active_task = session.exec(
            select(TranscodeTask).where(
                TranscodeTask.video_id == video.id,
                TranscodeTask.status.in_(["pending", "processing"])
            )
        ).first()

        if not active_task:
            # 异常：Video是pending但没有活跃任务
            owner = session.get(User, video.user_id)
            abnormal_list.append({
                "video_id": str(video.id),
                "title": video.title,
                "video_status": video.status,
                "owner": owner.username if owner else "",
                "problem": "Video状态为pending，但没有活跃的转码任务",
                "created_at": video.created_at.isoformat() if video.created_at else None
            })

    # 查找 Video.status='processing' 但任务已过期（超过30分钟无更新）
    processing_threshold = datetime.utcnow() - timedelta(minutes=30)
    processing_videos = session.exec(
        select(Video).where(Video.status == "processing", Video.is_deleted == False)
    ).all()

    for video in processing_videos:
        # 检查是否有活跃的转码任务
        active_task = session.exec(
            select(TranscodeTask).where(
                TranscodeTask.video_id == video.id,
                TranscodeTask.status == "processing"
            )
        ).first()

        if not active_task:
            owner = session.get(User, video.user_id)
            abnormal_list.append({
                "video_id": str(video.id),
                "title": video.title,
                "video_status": video.status,
                "owner": owner.username if owner else "",
                "problem": "Video状态为processing，但没有活跃的转码任务（可能已卡死）",
                "created_at": video.created_at.isoformat() if video.created_at else None
            })

    return {
        "abnormal_count": len(abnormal_list),
        "abnormal_list": abnormal_list
    }


@router.post("/transcode/fix-abnormal")
async def fix_abnormal_transcode_tasks(
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """
    修复所有转码异常状态：
    - pending但无任务的视频：重置并触发新任务
    - processing但无任务的视频：重置为pending并触发新任务
    """
    from tasks import transcode_video_task

    fixed_count = 0
    error_count = 0
    errors = []

    # 1. 处理 Video.status='pending' 但没有活跃任务的视频
    pending_videos = session.exec(
        select(Video).where(Video.status == "pending", Video.is_deleted == False)
    ).all()

    for video in pending_videos:
        active_task = session.exec(
            select(TranscodeTask).where(
                TranscodeTask.video_id == video.id,
                TranscodeTask.status.in_(["pending", "processing"])
            )
        ).first()

        if not active_task:
            try:
                # 创建新任务
                task = TranscodeTask(
                    video_id=video.id,
                    user_id=video.user_id,
                    status="pending",
                    priority=0,
                    priority_type="normal",
                    queue_name="default"
                )
                session.add(task)
                session.commit()

                # 触发转码任务（使用视频ID作为task_id防止重复）
                transcode_video_task.apply_async(args=[str(video.id)], task_id=str(video.id))
                fixed_count += 1
                logger.info(f"Fixed abnormal pending task for video {video.id}")
            except Exception as e:
                error_count += 1
                errors.append(f"视频 {video.id}: {str(e)}")
                session.rollback()

    # 2. 处理 Video.status='processing' 但没有活跃任务的视频
    processing_videos = session.exec(
        select(Video).where(Video.status == "processing", Video.is_deleted == False)
    ).all()

    for video in processing_videos:
        active_task = session.exec(
            select(TranscodeTask).where(
                TranscodeTask.video_id == video.id,
                TranscodeTask.status == "processing"
            )
        ).first()

        if not active_task:
            try:
                # 重置为 pending
                video.status = "pending"
                session.add(video)

                # 创建新任务
                task = TranscodeTask(
                    video_id=video.id,
                    user_id=video.user_id,
                    status="pending",
                    priority=0,
                    priority_type="normal",
                    queue_name="default"
                )
                session.add(task)
                session.commit()

                # 触发转码任务（使用视频ID作为task_id防止重复）
                transcode_video_task.apply_async(args=[str(video.id)], task_id=str(video.id))
                fixed_count += 1
                logger.info(f"Fixed abnormal processing task for video {video.id}")
            except Exception as e:
                error_count += 1
                errors.append(f"视频 {video.id}: {str(e)}")
                session.rollback()

    log_admin_action(session, admin.id, "fix_abnormal_transcode", None,
                     f"Fixed {fixed_count} abnormal tasks, {error_count} errors")
    session.commit()

    return {
        "fixed_count": fixed_count,
        "error_count": error_count,
        "errors": errors
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

    # 更新视频状态为 processing（立即显示转码中）
    video = session.get(Video, task.video_id)
    if video:
        video.status = "processing"
        session.add(video)

    session.add(task)
    session.commit()

    # 提交到 Celery（使用唯一的 task_id）
    video_id_str = str(task.video_id)
    from tasks import transcode_video_task
    new_task_id = f"{video_id_str}-bump-{task.bump_count}"
    transcode_video_task.apply_async(args=[video_id_str], task_id=new_task_id)

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

    video_id = str(task.video_id)

    # 终止 Celery 任务（无论状态如何，只要celery_task_id存在就尝试终止）
    if task.celery_task_id:
        try:
            celery_app.control.revoke(task.celery_task_id, terminate=True)
        except Exception:
            pass

    # 清理所有相关的 Redis 锁
    try:
        from tasks import get_redis_client
        redis_client = get_redis_client()
        redis_client.delete(f"transcode_lock:{video_id}")
        redis_client.delete(f"transcode_resume_percent:{video_id}")
        redis_client.delete(f"transcode_resume_resolution:{video_id}")
        redis_client.delete(f"transcode_resume_timestamp:{video_id}")
        redis_client.delete(f"transcode_resolution:{video_id}")
        redis_client.delete(f"transcode_timestamp:{video_id}")
    except Exception:
        pass

    # 清理缓存文件
    processed_dir = settings.PROCESSED_DIR / video_id
    if processed_dir.exists():
        shutil.rmtree(processed_dir)

    task.status = "cancelled"
    task.pause_percent = 0
    task.pause_resolution = None
    task.pause_timestamp = None
    task.celery_task_id = None
    session.add(task)

    # 重置视频状态为 failed
    video = session.get(Video, task.video_id)
    if video and video.status in ("processing", "paused", "pending"):
        video.status = "failed"
        video.progress = 0
        session.add(video)

    session.commit()

    return {"message": "Task cancelled"}


@router.post("/videos/{video_id}/cancel-transcode")
async def cancel_video_transcode(
    video_id: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """
    取消视频转码（通过video_id，不依赖task_id）
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # 清理缓存文件
    processed_dir = settings.PROCESSED_DIR / str(video_id)
    if processed_dir.exists():
        shutil.rmtree(processed_dir)

    # 清理所有相关的 Redis 锁
    try:
        from tasks import get_redis_client
        redis_client = get_redis_client()
        redis_client.delete(f"transcode_lock:{video_id}")
        redis_client.delete(f"transcode_resume_percent:{video_id}")
        redis_client.delete(f"transcode_resume_resolution:{video_id}")
        redis_client.delete(f"transcode_resume_timestamp:{video_id}")
        redis_client.delete(f"transcode_resolution:{video_id}")
        redis_client.delete(f"transcode_timestamp:{video_id}")
    except Exception:
        pass

    # 查找并删除该视频的所有转码任务
    tasks = session.exec(select(TranscodeTask).where(
        TranscodeTask.video_id == video_id,
        TranscodeTask.status.in_(["pending", "processing", "paused"])
    )).all()

    for task in tasks:
        # 终止 Celery 任务
        if task.celery_task_id:
            try:
                celery_app.control.revoke(task.celery_task_id, terminate=True)
            except Exception:
                pass
        session.delete(task)

    # 重置视频状态
    video.status = "failed"
    video.progress = 0
    session.add(video)

    session.commit()

    return {"message": "Transcode cancelled"}


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

    # 同时更新视频状态
    if video:
        video.status = "paused"
        session.add(video)

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
        redis_client.set(f"transcode_resume_percent:{task.video_id}", task.pause_percent or 0, ex=3600)
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

    # 视频状态设为 processing（立即显示转码中），同时恢复进度
    video = session.get(Video, task.video_id)
    if video:
        video.status = "processing"
        video.progress = task.pause_percent or 0  # 恢复实际进度
        session.add(video)

    session.commit()

    # 重新提交Celery任务，传递恢复参数（使用唯一task_id）
    new_task_id = f"{task.video_id}-resume"
    transcode_video_task.apply_async(args=[str(task.video_id)], kwargs={"resume": "resume"}, task_id=new_task_id)

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

    transcode_video_task.apply_async(args=[video_id], task_id=video_id)

    return {"message": "Transcode retry scheduled", "retry_count": task.retry_count if task else 0}


@router.post("/videos/{video_id}/transcode")
async def trigger_transcode(
    video_id: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """手动触发视频转码（删除旧文件后重新转码）"""
    # 验证 video_id 不是 "null" 或空字符串
    if not video_id or video_id == "null" or video_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid video ID")

    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # 1. 删除旧转码文件
    import shutil
    from pathlib import Path
    processed_dir = Path(settings.PROCESSED_DIR) / video_id
    if processed_dir.exists():
        shutil.rmtree(processed_dir)

    # 2. 重置视频状态
    video.status = "pending"
    video.progress = 0
    session.add(video)

    # 3. 删除旧的 cancelled/failed/completed 任务
    old_tasks = session.exec(
        select(TranscodeTask).where(
            TranscodeTask.video_id == video.id,
            TranscodeTask.status.in_(["cancelled", "failed", "completed"])
        )
    ).all()
    for old_task in old_tasks:
        session.delete(old_task)

    # 4. 创建/更新转码任务记录
    existing_task = session.exec(
        select(TranscodeTask).where(
            TranscodeTask.video_id == video.id,
            TranscodeTask.status.in_(["pending", "processing"])
        )
    ).first()

    if existing_task:
        existing_task.status = "pending"
        existing_task.priority = 0
        existing_task.priority_type = "normal"
        existing_task.queue_name = "default"
        existing_task.started_at = None
        existing_task.completed_at = None
        session.add(existing_task)
        task = existing_task
    else:
        task = TranscodeTask(
            video_id=video.id,
            user_id=video.user_id,
            status="pending",
            priority=0,
            priority_type="normal",
            queue_name="default"
        )
        session.add(task)

    session.commit()

    # 5. 触发 Celery 任务（使用唯一的task_id）
    import uuid
    celery_task_id = f"{video_id}-transcode-{uuid.uuid4().hex[:8]}"
    transcode_video_task.apply_async(args=[video_id], task_id=celery_task_id)

    return {"message": "Transcode triggered", "task_id": video_id}


@router.post("/videos/{video_id}/reextract-subtitles")
async def reextract_subtitles(
    video_id: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """重新提取视频字幕（使用正确的语言代码命名）"""
    from tasks import reextract_subtitles_for_video

    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if not video.original_file_path:
        raise HTTPException(status_code=400, detail="Original file not found")

    if not video.has_embedded_subtitles:
        raise HTTPException(status_code=400, detail="Video has no embedded subtitles to re-extract")

    result = reextract_subtitles_for_video(video_id)

    log_admin_action(
        session, admin.id, "reextract_subtitles",
        video_id,
        f"Re-extracted {result.get('extracted', 0)} subtitles: {result.get('languages', [])}"
    )

    return result


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

    days_threshold = get_runtime_config("COLD_STORAGE_TRIGGER_DAYS", 180)
    views_threshold = get_runtime_config("COLD_STORAGE_TRIGGER_VIEWS", 10)
    cutoff_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_threshold)

    candidates_count = session.exec(
        select(func.count()).select_from(Video).where(
            Video.is_cold == False,
            Video.created_at < cutoff_date,
            Video.views < views_threshold,
            Video.status == "completed",
            Video.is_approved == "approved"
        )
    ).one()

    active_count = session.exec(
        select(func.count()).select_from(Video).where(Video.is_cold == False, Video.is_deleted == False)
    ).one()

    cold_videos_query = session.exec(
        select(Video).where(Video.is_cold == True, Video.is_deleted == False).order_by(desc(Video.created_at)).limit(20)
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
    """扫描预览 - 获取符合条件的冷存储候选视频（不执行迁移）"""
    days_threshold = get_runtime_config("COLD_STORAGE_TRIGGER_DAYS", 180)
    views_threshold = get_runtime_config("COLD_STORAGE_TRIGGER_VIEWS", 10)
    cutoff_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_threshold)

    candidates = session.exec(
        select(Video).where(
            Video.is_cold == False,
            Video.is_deleted == False,
            Video.created_at < cutoff_date,
            Video.views < views_threshold,
            Video.status == "completed",
            Video.is_approved == "approved"
        ).order_by(Video.created_at)
    ).all()

    result = []
    total_size = 0
    for v in candidates:
        video_dict = v.model_dump()
        owner = session.get(User, v.user_id)
        video_dict["owner"] = owner.username if owner else ""
        video_dict["age_days"] = (datetime.utcnow() - v.created_at).days

        # 计算文件大小
        try:
            original_path = settings.fs_path(v.original_file_path)
            file_size = original_path.stat().st_size if original_path.exists() else 0
            video_dict["file_size"] = file_size
            total_size += file_size
        except Exception:
            video_dict["file_size"] = 0

        result.append(video_dict)

    return {"count": len(result), "total_size": total_size, "candidates": result}


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
    """执行迁移 - 将所有符合条件的视频迁移到冷存储"""
    days_threshold = get_runtime_config("COLD_STORAGE_TRIGGER_DAYS", 180)
    views_threshold = get_runtime_config("COLD_STORAGE_TRIGGER_VIEWS", 10)
    cutoff_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_threshold)

    candidates = session.exec(
        select(Video).where(
            Video.is_cold == False,
            Video.is_deleted == False,
            Video.created_at < cutoff_date,
            Video.views < views_threshold,
            Video.status == "completed",
            Video.is_approved == "approved"
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

@router.get("/videos/stats")
async def get_video_stats(
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """获取视频统计信息"""
    from datetime import datetime, timedelta

    # 总视频数
    total = session.exec(
        select(func.count()).select_from(Video).where(Video.is_deleted == False)
    ).one()

    # 转码中
    processing = session.exec(
        select(func.count()).select_from(Video).where(
            Video.is_deleted == False,
            Video.status == "processing"
        )
    ).one()

    # 待处理
    pending = session.exec(
        select(func.count()).select_from(Video).where(
            Video.is_deleted == False,
            Video.status == "pending"
        )
    ).one()

    # 今日完成（使用 TranscodeTask 的 completed_at）
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    completed_today = session.exec(
        select(func.count()).select_from(TranscodeTask).where(
            TranscodeTask.status == "completed",
            TranscodeTask.completed_at >= today_start
        )
    ).one()

    return {
        "total": total,
        "processing": processing,
        "pending": pending,
        "completed_today": completed_today
    }


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
        select(Video).where(Video.is_deleted == False).order_by(desc(Video.created_at)).offset(offset).limit(size)
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
        # 查询该视频最新的转码任务
        task = session.exec(
            select(TranscodeTask).where(
                TranscodeTask.video_id == v.id
            ).order_by(desc(TranscodeTask.created_at)).limit(1)
        ).first()
        if task:
            video_dict["task"] = {
                "id": str(task.id),
                "status": task.status,
                "pause_percent": task.pause_percent,
            }
        else:
            video_dict["task"] = None
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

    video.is_approved = "banned"
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

    # 清除 Redis 缓存
    from cache_manager import get_cache
    cache = get_cache()
    cache.delete_trending_video(str(video.id))  # 从热门列表删除
    cache.clear_slot_cache("personalized")  # 清除个性化推荐缓存

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

    video.is_approved = "approved"
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

    # 将视频加入热门列表
    from cache_manager import get_cache
    cache = get_cache()
    score = (video.views ** 0.5) + (video.like_count or 0) * 2 + (video.favorite_count or 0) * 3
    cache.zadd_trending(str(video.id), score)
    if video.category_id:
        cache.zadd_trending_category(video.category_id, str(video.id), score)

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


@router.post("/videos/{video_id}/approval")
async def update_video_approval(
    video_id: str,
    approval_status: str = Body(..., description="审核状态: pending, approved, banned, appealing"),
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """更新视频审核状态"""
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if approval_status not in ("pending", "approved", "banned", "appealing"):
        raise HTTPException(status_code=400, detail="Invalid approval status")

    old_status = video.is_approved
    video.is_approved = approval_status
    session.add(video)

    audit_log = VideoAuditLog(
        video_id=video_id,
        operator_id=admin.id,
        action="approval_change",
        reason=f"Approval status changed from {old_status} to {approval_status}"
    )
    session.add(audit_log)

    # 发送通知给视频所有者
    if video.user_id:
        status_msg = {
            "approved": "已通过审核并上架",
            "banned": "已被下架",
            "appealing": "申诉中",
            "pending": "待审核"
        }.get(approval_status, f"状态变更为 {approval_status}")

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

    log_admin_action(session, admin.id, "update_video_approval", video_id, f"Approval: {old_status} -> {approval_status}")

    return {"message": "Video approval status updated"}


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


@router.post("/comments/{comment_id}/restore")
async def restore_comment_admin(
    comment_id: int,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """恢复评论（管理员）"""
    comment = session.get(Comment, comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    comment.is_deleted = False
    comment.deleted_by = None
    session.add(comment)
    session.commit()

    log_admin_action(session, admin.id, "restore_comment", str(comment_id), "Restored by admin")
    session.commit()

    return {"message": "Comment restored"}


# ==================== 推荐重算 ====================

@router.post("/recommendations/recompute-trending")
async def recompute_trending(
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """手动触发热门统计重算"""
    from tasks import compute_daily_trending_task, compute_category_trending_task

    try:
        # 同步执行热门统计（使用 .apply() 代替直接调用）
        result_daily = compute_daily_trending_task.apply()
        result_category = compute_category_trending_task.apply()

        return {
            "message": "Trending recomputed",
            "daily_trending": result_daily.result if hasattr(result_daily, 'result') else result_daily,
            "category_trending": result_category.result if hasattr(result_category, 'result') else result_category
        }
    except Exception as e:
        return {"message": f"Error: {str(e)}", "status": "error"}


@router.post("/recommendations/recompute-user/{user_id}")
async def recompute_user_recommendations(
    user_id: str,
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """手动触发指定用户的个性化推荐重算"""
    from recommendation_engine import compute_user_recommendation_scores

    try:
        result = asyncio.run(
            compute_user_recommendation_scores(session, UUID(user_id))
        )
        return {
            "message": f"User recommendations recomputed for {user_id}",
            "processed": result
        }
    except Exception as e:
        return {"message": f"Error: {str(e)}", "status": "error"}


@router.post("/recommendations/recompute-all")
async def recompute_all_recommendations(
    admin: User = Depends(PermissionChecker("video:audit")),
    session: Session = Depends(get_session)
):
    """手动触发所有用户的个性化推荐重算"""
    from tasks import compute_all_recommendation_scores

    try:
        # 这是一个长时间运行的任务，返回任务ID
        result = compute_all_recommendation_scores.delay()
        return {
            "message": "All user recommendations recompute task started",
            "task_id": result.id
        }
    except Exception as e:
        return {"message": f"Error: {str(e)}", "status": "error"}
