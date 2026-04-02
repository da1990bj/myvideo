"""
MyVideo Configuration Module

所有配置值都在 .env 文件中定义，此模块负责读取和提供类型安全的访问接口。

Usage:
    from app.config import settings

    # Database
    settings.DATABASE_URL

    # Paths
    settings.BASE_DIR           # 项目根目录
    settings.UPLOADS_DIR         # 视频上传目录
"""

import logging
from pathlib import Path
from typing import Optional
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class MyVideoSettings(BaseSettings):
    """
    配置读取器 - 所有配置值从 .env 文件读取

    注意：不再有硬编码默认值，所有配置必须在 .env 中显式定义
    """

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # ==================== 基础配置 ====================
    MYVIDEO_ROOT: str

    # ==================== 数据库 ====================
    DATABASE_HOST: str
    DATABASE_PORT: int
    DATABASE_USER: str
    DATABASE_PASSWORD: str
    DATABASE_NAME: str
    DATABASE_ECHO: bool = False

    # ==================== Redis ====================
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None

    # ==================== 安全 / JWT ====================
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ==================== 应用服务器 ====================
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_DEBUG: bool = False

    # ==================== 文件存储目录 ====================
    STATIC_SUBDIR: str = "static"
    UPLOADS_SUBDIR: str = "static/videos/uploads"
    PROCESSED_SUBDIR: str = "static/videos/processed"
    THUMBNAILS_SUBDIR: str = "static/thumbnails"
    AVATARS_SUBDIR: str = "static/avatars"
    DATA_SUBDIR: str = "data"

    # ==================== URL 路径前缀 ====================
    THUMBNAILS_URL: str = "/static/thumbnails"
    VIDEOS_URL: str = "/static/videos"
    AVATARS_URL: str = "/static/avatars"

    # ==================== CORS ====================
    CORS_ORIGINS: str = "*"
    CORS_CREDENTIALS: bool = True
    CORS_METHODS: str = "*"
    CORS_HEADERS: str = "*"

    # ==================== Celery ====================
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""

    # ==================== 日志 ====================
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Optional[str] = None

    # ==================== 敏感词 ====================
    SENSITIVE_WORDS_FILE: Optional[str] = None

    # ==================== 冷存储 ====================
    COLD_STORAGE_ENABLED: bool = False
    COLD_STORAGE_TRIGGER_DAYS: int = 180
    COLD_STORAGE_TRIGGER_VIEWS: int = 10
    COLD_STORAGE_PATH_ROOT: str = "/data/myvideo/cold_storage"

    # ==================== 存储迁移 ====================
    # 迁移间隔时间（秒），控制迁移速度，避免 CPU/磁盘 占用过高
    # 值越大速度越慢，0 表示不限制
    STORAGE_MIGRATION_DELAY: float = 0.5

    # ==================== 存储后端 ====================
    # 可选值: local, s3, oss
    STORAGE_BACKEND: str = "local"

    # S3 配置 (当 STORAGE_BACKEND=s3 时使用)
    S3_ENDPOINT_URL: Optional[str] = None
    S3_ACCESS_KEY_ID: Optional[str] = None
    S3_SECRET_ACCESS_KEY: Optional[str] = None
    S3_BUCKET_NAME: Optional[str] = None
    S3_REGION_NAME: str = "us-east-1"
    S3_PREFIX: str = ""

    # 阿里云 OSS 配置 (当 STORAGE_BACKEND=oss 时使用)
    OSS_ENDPOINT: Optional[str] = None
    OSS_ACCESS_KEY_ID: Optional[str] = None
    OSS_SECRET_ACCESS_KEY: Optional[str] = None
    OSS_BUCKET_NAME: Optional[str] = None
    OSS_REGION_NAME: str = "cn-hangzhou"
    OSS_PREFIX: str = ""

    # ==================== 计算属性 ====================

    @property
    def BASE_DIR(self) -> Path:
        """项目根目录"""
        return Path(self.MYVIDEO_ROOT)

    @property
    def STATIC_DIR(self) -> Path:
        """静态文件目录"""
        return self.BASE_DIR / self.STATIC_SUBDIR

    @property
    def UPLOADS_DIR(self) -> Path:
        """视频上传目录"""
        return self.BASE_DIR / self.UPLOADS_SUBDIR

    @property
    def PROCESSED_DIR(self) -> Path:
        """转码视频目录"""
        return self.BASE_DIR / self.PROCESSED_SUBDIR

    @property
    def THUMBNAILS_DIR(self) -> Path:
        """缩略图目录"""
        return self.BASE_DIR / self.THUMBNAILS_SUBDIR

    @property
    def THUMBNAILS_TEMP_DIR(self) -> Path:
        """临时缩略图目录"""
        return self.THUMBNAILS_DIR / "temp"

    @property
    def AVATARS_DIR(self) -> Path:
        """头像目录"""
        return self.BASE_DIR / self.AVATARS_SUBDIR

    @property
    def DATA_DIR(self) -> Path:
        """数据目录"""
        return self.BASE_DIR / self.DATA_SUBDIR

    @property
    def DATABASE_URL(self) -> str:
        """PostgreSQL 连接 URL"""
        return f"postgresql://{self.DATABASE_USER}:{self.DATABASE_PASSWORD}@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"

    @property
    def REDIS_URL(self) -> str:
        """Redis 连接 URL"""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def CELERY_BROKER(self) -> str:
        """Celery 消息代理"""
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def CELERY_BACKEND(self) -> str:
        """Celery 结果后端"""
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL

    @property
    def LOG_FILE_PATH(self) -> Path:
        """日志文件路径"""
        if self.LOG_FILE:
            return Path(self.LOG_FILE)
        return self.BASE_DIR / "server.log"

    @property
    def SENSITIVE_WORDS_PATH(self) -> Path:
        """敏感词文件路径"""
        if self.SENSITIVE_WORDS_FILE:
            return Path(self.SENSITIVE_WORDS_FILE)
        return self.DATA_DIR / "sensitive_words.txt"

    @property
    def COLD_STORAGE_PATH(self) -> Path:
        """冷存储根目录"""
        return Path(self.COLD_STORAGE_PATH_ROOT)

    @property
    def COLD_STORAGE_UPLOADS_DIR(self) -> Path:
        """冷存储 - 原始视频"""
        return self.COLD_STORAGE_PATH / "videos" / "uploads"

    @property
    def COLD_STORAGE_PROCESSED_DIR(self) -> Path:
        """冷存储 - 转码视频"""
        return self.COLD_STORAGE_PATH / "videos" / "processed"

    # ==================== 工具方法 ====================

    def fs_path(self, url_path: str) -> Path:
        """将 URL 路径转换为文件系统路径"""
        parts = url_path.lstrip("/").split("/", 1)
        if len(parts) < 2:
            return self.BASE_DIR
        if parts[0] == "static":
            return self.BASE_DIR / "static" / parts[1]
        return self.BASE_DIR / url_path.lstrip("/")

    def url_path(self, relative_path: str) -> str:
        """将相对路径转换为 URL 路径"""
        if relative_path.startswith("/"):
            return relative_path
        if relative_path.startswith("thumbnails"):
            return f"{self.THUMBNAILS_URL}/{relative_path.replace('thumbnails/', '')}"
        elif relative_path.startswith("videos"):
            return f"{self.VIDEOS_URL}/{relative_path.replace('videos/', '')}"
        elif relative_path.startswith("avatars"):
            return f"{self.AVATARS_URL}/{relative_path.replace('avatars/', '')}"
        return f"/static/{relative_path}"

    def ensure_dirs(self) -> None:
        """确保所有必要目录存在"""
        dirs = [
            self.UPLOADS_DIR,
            self.PROCESSED_DIR,
            self.THUMBNAILS_DIR,
            self.THUMBNAILS_TEMP_DIR,
            self.AVATARS_DIR,
            self.DATA_DIR,
        ]
        if self.COLD_STORAGE_ENABLED:
            dirs.extend([
                self.COLD_STORAGE_UPLOADS_DIR,
                self.COLD_STORAGE_PROCESSED_DIR,
            ])
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured directory exists: {d}")


@lru_cache()
def get_settings() -> MyVideoSettings:
    """获取配置单例"""
    return MyVideoSettings()


# 模块级单例
settings = get_settings()
