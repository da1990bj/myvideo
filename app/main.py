"""
MyVideo v3.0 - FastAPI 主应用

架构变更：
- 所有 API 路由已拆分到 app/routers/ 目录下的模块化路由
- WebSocket 通过 Redis Adapter 支持多节点部署
- Celery 任务通过 Redis Pub/Sub 与 WebSocket 解耦
"""
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from sqlmodel import Session, select

from config import settings
from database import engine, init_db
from data_models import User, Notification, UserRole, Role
from socketio_handler import manager
import socketio_handler
import logging

logger = logging.getLogger(__name__)


# ==================== WebSocket 配置 ====================

# 尝试启用 Redis Adapter 以支持多节点部署
try:
    client_manager = socketio.AsyncRedisManager(settings.REDIS_URL)
    logger.info("✅ WebSocket Redis Adapter 已启用 (多节点支持)")
except Exception as e:
    logger.warning(f"⚠️ WebSocket Redis Adapter 启用失败: {e}，使用内存模式")
    client_manager = None

sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins=['*'],
    cors_credentials=True,
    ping_timeout=60,
    ping_interval=25,
    engineio_logger=False,
    logger=False,
    client_manager=client_manager
)


# ==================== 路由模块导入 ====================

from routers import (
    auth_router,
    videos_router,
    social_router,
    admin_router,
    collections_router,
    recommendations_router,
)


# ==================== FastAPI 应用 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化
    init_db()
    settings.ensure_dirs()

    from init_data import init_categories, init_recommendation_slots, init_recommendation_config
    init_categories()
    init_recommendation_slots()
    init_recommendation_config()

    # 初始化缓存系统
    try:
        from cache_manager import get_cache
        cache = get_cache()
        logger.info(f"✅ 缓存系统已初始化 ({'Redis' if cache.enabled else '本地缓存'})")
    except Exception as e:
        logger.warning(f"⚠️ 缓存初始化失败: {e}")

    # 启动 Redis Pub/Sub 监听器（接收 Celery 任务进度推送）
    try:
        listener_coroutine = await socketio_handler.start_redis_listener(sio, settings.REDIS_URL)
        import asyncio
        asyncio.create_task(listener_coroutine)
        logger.info("✅ Redis Pub/Sub 监听器已启动")
    except Exception as e:
        logger.warning(f"⚠️ Redis Pub/Sub 监听器启动失败: {e}")

    logger.info("✅ MyVideo v3.0 startup complete - Modular architecture + Redis Adapter + Pub/Sub")

    yield

    # 关闭时清理


app = FastAPI(
    title="MyVideo Backend",
    version="3.0.0",
    lifespan=lifespan
)

# 挂载静态文件
app.mount("/static", StaticFiles(directory=str(settings.STATIC_DIR)), name="static")

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由模块
app.include_router(auth_router)
app.include_router(videos_router)
app.include_router(social_router)
app.include_router(admin_router)
app.include_router(collections_router)
app.include_router(recommendations_router)


# ==================== SocketIO 应用（必须在 app 和路由注册后创建）====================

socketio_app = socketio.ASGIApp(sio, app)


# ==================== 基础路由 ====================

@app.get("/")
async def root():
    """API 根路径"""
    return {"message": "MyVideo API v3.0 is running!", "version": "3.0.0"}


@app.get("/system/config")
async def get_public_system_config():
    """获取公开的系统配置"""
    from sqlmodel import select
    from database import get_session
    from data_models import SystemConfig

    with Session(engine) as session:
        configs = session.exec(select(SystemConfig)).all()
        result = {}
        for c in configs:
            result[c.key] = c.value
        return result


# ==================== WebSocket 事件处理 ====================

@sio.event
async def connect(sid, environ, auth):
    """WebSocket 连接事件"""
    try:
        token = None
        if auth and isinstance(auth, dict):
            token = auth.get('token')

        if not token:
            logger.warning(f"WebSocket connect attempt without token (SID: {sid})")
            raise ConnectionRefusedError('No token provided')

        try:
            from security import SECRET_KEY, ALGORITHM
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
            if username is None:
                raise ConnectionRefusedError('Invalid token: no username')

            with Session(engine) as session:
                user = session.exec(select(User).where(User.username == username)).first()
                if not user:
                    raise ConnectionRefusedError('User not found')

                await manager.connect(str(user.id), sid)
                logger.info(f"✅ WebSocket connected: User {user.username} ({user.id}), SID: {sid}")

                # 检查用户是否有超级管理员权限（任何角色的 permissions == "*"）
                is_superadmin = False
                user_roles = session.exec(select(UserRole).where(UserRole.user_id == user.id)).all()
                for ur in user_roles:
                    role = session.get(Role, ur.role_id)
                    if role and role.permissions == "*":
                        is_superadmin = True
                        break

                if user.is_admin or is_superadmin:
                    await sio.enter_room(sid, "admin")
                    logger.info(f"Admin user {user.username} joined admin room")

        except JWTError as e:
            logger.warning(f"JWT validation failed in WebSocket connect (SID: {sid}): {str(e)}")
            raise ConnectionRefusedError('Invalid JWT token')

    except ConnectionRefusedError as e:
        logger.warning(f"WebSocket connection refused (SID: {sid}): {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in WebSocket connect handler (SID: {sid}): {str(e)}")
        raise ConnectionRefusedError('Connection error')


@sio.event
async def disconnect(sid):
    """WebSocket 断开连接事件"""
    try:
        user_id = await manager.disconnect_by_sid(sid)
        if user_id:
            logger.info(f"User {user_id} disconnected from WebSocket (SID: {sid})")
    except Exception as e:
        logger.error(f"Error in WebSocket disconnect handler: {e}")


@sio.event
async def ping(sid):
    """心跳检测"""
    return {"pong": True}


@sio.event
async def get_connection_info(sid):
    """获取连接统计信息"""
    return manager.get_connection_info()
