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

    # ==================== 分享设置 ====================
    # 分享链接使用的公开访问地址（留空则使用 APP_HOST:APP_PORT）
    SHARE_BASE_URL: str = ""

    # ==================== 文件存储目录 ====================
    STATIC_SUBDIR: str = "static"
    UPLOADS_SUBDIR: str = "static/videos/uploads"
    PROCESSED_SUBDIR: str = "static/videos/processed"
    THUMBNAILS_SUBDIR: str = "static/thumbnails"
    AVATARS_SUBDIR: str = "static/avatars"
    DATA_SUBDIR: str = "data"

    # ==================== 上传限制 ====================
    # 视频上传大小限制（单位：MB），0表示不限制
    MAX_UPLOAD_SIZE_MB: int = 2048

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

    # ==================== 站点信息 ====================
    SITE_NAME: str = "MyVideo"

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

    # ==================== 转码队列优先级 ====================
    # 转码并发数（同时进行的转码任务数）
    TRANSCODE_CONCURRENCY: int = 4
    # 普通用户 aging 增速（每小时增加的优先级）
    TRANSCODE_AGING_RATE: float = 0.5
    # 最大优先级分数
    TRANSCODE_MAX_PRIORITY: int = 40
    # VIP用户基础优先级
    TRANSCODE_VIP_BASE_PRIORITY: int = 10
    # 付费加速用户基础优先级
    TRANSCODE_PAID_BASE_PRIORITY: int = 30
    # 插队消耗积分
    TRANSCODE_BUMP_COST: int = 5

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
        subdir = get_runtime_config("UPLOADS_SUBDIR", self.UPLOADS_SUBDIR)
        return self.BASE_DIR / subdir

    @property
    def PROCESSED_DIR(self) -> Path:
        """转码视频目录"""
        subdir = get_runtime_config("PROCESSED_SUBDIR", self.PROCESSED_SUBDIR)
        return self.BASE_DIR / subdir

    @property
    def THUMBNAILS_DIR(self) -> Path:
        """缩略图目录"""
        subdir = get_runtime_config("THUMBNAILS_SUBDIR", self.THUMBNAILS_SUBDIR)
        return self.BASE_DIR / subdir

    @property
    def THUMBNAILS_TEMP_DIR(self) -> Path:
        """临时缩略图目录"""
        return self.THUMBNAILS_DIR / "temp"

    @property
    def AVATARS_DIR(self) -> Path:
        """头像目录"""
        subdir = get_runtime_config("AVATARS_SUBDIR", self.AVATARS_SUBDIR)
        return self.BASE_DIR / subdir

    @property
    def DATA_DIR(self) -> Path:
        """数据目录"""
        subdir = get_runtime_config("DATA_SUBDIR", self.DATA_SUBDIR)
        return self.BASE_DIR / subdir

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
        # 如果是绝对路径，直接返回（向后兼容）
        if url_path.startswith("/data/myvideo/"):
            return Path(url_path)
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

    def setup_logging(self) -> None:
        """配置统一日志格式，添加时间戳

        必须在应用启动早期调用，通常在 main.py 的 lifespan 或模块导入时调用
        """
        import logging

        log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        date_format = "%Y-%m-%d %H:%M:%S"

        # 文件Handler - 写入 server.log
        file_handler = logging.FileHandler(self.LOG_FILE_PATH)
        file_handler.setFormatter(logging.Formatter(log_format, date_format))
        file_handler.setLevel(logging.INFO)

        # 控制台Handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(log_format, date_format))
        console_handler.setLevel(logging.INFO)

        # 根日志器配置
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)

        # 第三方库日志级别调整
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # 降低访问日志
        logging.getLogger("uvicorn.error").setLevel(logging.INFO)
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)  # SQL日志太多
        logging.getLogger("PIL").setLevel(logging.WARNING)  # 图片处理日志太多

        logger.info(f"Logging configured: file={self.LOG_FILE_PATH}")

    def update_logging_level(self) -> None:
        """热更新日志级别（从运行时配置读取并应用到所有handler）"""
        import logging

        # 获取运行时日志级别配置
        log_level_str = get_runtime_config("LOG_LEVEL", "INFO")
        try:
            log_level = getattr(logging, log_level_str.upper(), logging.INFO)
        except Exception:
            log_level = logging.INFO

        # 应用到根日志器
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)

        # 应用到所有已配置的handler
        for handler in root_logger.handlers:
            handler.setLevel(log_level)

        # 同时更新第三方库的日志级别（基于新的日志级别降低）
        if log_level <= logging.DEBUG:
            logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
            logging.getLogger("uvicorn.error").setLevel(logging.INFO)
            logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        else:
            # 非DEBUG模式下，进一步降低第三方库日志
            logging.getLogger("uvicorn.access").setLevel(logging.ERROR)
            logging.getLogger("uvicorn.error").setLevel(logging.ERROR)
            logging.getLogger("sqlalchemy.engine").setLevel(logging.ERROR)

        logger.info(f"Logging level updated: {log_level_str}")


@lru_cache()
def get_settings() -> MyVideoSettings:
    """获取配置单例"""
    return MyVideoSettings()


# 模块级单例
settings = get_settings()


# ==================== 运行时配置读取（支持数据库覆盖）====================
from typing import Any, Dict

# 运行时配置缓存（从数据库读取，热更新用）
_runtime_config_cache: Dict[str, Any] = {}
_cache_loaded: bool = False


def _load_runtime_config() -> None:
    """从数据库加载所有运行时配置到缓存（仅启动时或热更新时调用）"""
    global _runtime_config_cache, _cache_loaded
    try:
        from sqlmodel import Session, select
        from data_models import SystemConfig
        from database import engine

        with Session(engine) as session:
            configs = session.exec(select(SystemConfig)).all()
            _runtime_config_cache = {}
            for c in configs:
                _runtime_config_cache[c.key] = _parse_config_value(c.value)
            _cache_loaded = True
            logger.info(f"运行时配置已加载，共 {len(_runtime_config_cache)} 项")
    except Exception as e:
        logger.warning(f"加载运行时配置失败: {e}")


def _parse_config_value(value: str) -> Any:
    """根据值内容推断并转换类型"""
    if not isinstance(value, str):
        return value
    # bool
    if value.lower() in ("true", "1", "yes"):
        return True
    if value.lower() in ("false", "0", "no"):
        return False
    # int
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    # float
    try:
        return float(value)
    except ValueError:
        return value


def get_runtime_config(key: str, default: Any = None) -> Any:
    """
    获取运行时配置（从缓存读取，无则从数据库加载）

    Args:
        key: 配置键名
        default: 默认值（当缓存和数据库都没有时返回此值）

    Returns:
        配置值（自动转换类型）
    """
    global _cache_loaded
    if not _cache_loaded:
        _load_runtime_config()
    return _runtime_config_cache.get(key, default)


def reload_runtime_config() -> None:
    """
    热更新：清除缓存并重新加载运行时配置（类似 nginx -s reload）

    调用此函数后，所有 get_runtime_config() 将读取新值
    """
    global _cache_loaded
    _cache_loaded = False
    _load_runtime_config()
    logger.info("运行时配置已热更新")

    # 热更新日志级别
    settings.update_logging_level()


def _get_config_override(key: str, default: any, session=None) -> any:
    """
    内部辅助：从数据库 SystemConfig 获取配置覆盖值

    Args:
        key: 配置键名
        default: 默认值（用于推断类型）
        session: 可选的数据库会话（如果传入则复用，否则创建新的）

    Returns:
        覆盖值或默认值
    """
    try:
        from sqlmodel import Session, select
        from data_models import SystemConfig
        from database import engine

        if session is None:
            should_close = True
            session = Session(engine)
        else:
            should_close = False

        try:
            config = session.exec(select(SystemConfig).where(SystemConfig.key == key)).first()
            if config:
                # 根据默认值类型转换
                if isinstance(default, bool):
                    return config.value.lower() in ("true", "1", "yes")
                elif isinstance(default, int):
                    return int(config.value)
                elif isinstance(default, float):
                    return float(config.value)
                return config.value
        finally:
            if should_close:
                session.close()
    except Exception:
        pass
    return default


def get_cold_storage_config() -> dict:
    """
    从数据库获取冷存储配置，支持运行时覆盖

    Returns:
        dict with keys: enabled, trigger_days, trigger_views, path_root
    """
    return {
        "enabled": _get_config_override("COLD_STORAGE_ENABLED", settings.COLD_STORAGE_ENABLED),
        "trigger_days": _get_config_override("COLD_STORAGE_TRIGGER_DAYS", settings.COLD_STORAGE_TRIGGER_DAYS),
        "trigger_views": _get_config_override("COLD_STORAGE_TRIGGER_VIEWS", settings.COLD_STORAGE_TRIGGER_VIEWS),
        "path_root": _get_config_override("COLD_STORAGE_PATH_ROOT", settings.COLD_STORAGE_PATH_ROOT),
    }


def get_storage_migration_delay() -> float:
    """
    从数据库获取存储迁移间隔时间（秒）

    Returns:
        迁移间隔时间
    """
    return _get_config_override("STORAGE_MIGRATION_DELAY", settings.STORAGE_MIGRATION_DELAY)


def get_log_level() -> str:
    """
    从数据库获取日志级别

    Returns:
        日志级别字符串 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    return _get_config_override("LOG_LEVEL", settings.LOG_LEVEL)


def get_transcode_config() -> dict:
    """
    从数据库获取转码队列配置，支持运行时覆盖

    Returns:
        dict with keys: concurrency, aging_rate, max_priority, vip_base_priority, paid_base_priority, bump_cost
    """
    return {
        "concurrency": _get_config_override("TRANSCODE_CONCURRENCY", settings.TRANSCODE_CONCURRENCY),
        "aging_rate": _get_config_override("TRANSCODE_AGING_RATE", settings.TRANSCODE_AGING_RATE),
        "max_priority": _get_config_override("TRANSCODE_MAX_PRIORITY", settings.TRANSCODE_MAX_PRIORITY),
        "vip_base_priority": _get_config_override("TRANSCODE_VIP_BASE_PRIORITY", settings.TRANSCODE_VIP_BASE_PRIORITY),
        "paid_base_priority": _get_config_override("TRANSCODE_PAID_BASE_PRIORITY", settings.TRANSCODE_PAID_BASE_PRIORITY),
        "bump_cost": _get_config_override("TRANSCODE_BUMP_COST", settings.TRANSCODE_BUMP_COST),
    }
