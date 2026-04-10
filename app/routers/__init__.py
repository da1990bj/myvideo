"""
路由模块 - 按功能域拆分的所有 API 路由
"""
from .auth import router as auth_router
from .videos import router as videos_router
from .social import router as social_router
from .admin import router as admin_router
from .collections import router as collections_router
from .recommendations import router as recommendations_router
from .cast import router as cast_router
from .categories import router as categories_router

__all__ = [
    "auth_router",
    "videos_router",
    "social_router",
    "admin_router",
    "collections_router",
    "recommendations_router",
    "cast_router",
    "categories_router",
]
