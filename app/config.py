"""
MyVideo Configuration Module

All application settings are centralized here.
Supports environment variables with sensible defaults for local development.

Usage:
    from app.config import settings

    # Database
    settings.DATABASE_URL

    # Paths
    settings.BASE_DIR           # /data/myvideo (project root)
    settings.STATIC_DIR         # /data/myvideo/static
    settings.UPLOADS_DIR        # /data/myvideo/static/videos/uploads

    # Helpers
    settings.fs_path("/static/thumbnails/image.jpg")  # -> Path
    settings.url_path("thumbnails/image.jpg")         # -> "/static/thumbnails/image.jpg"
"""

import os
import logging
from pathlib import Path
from typing import Optional
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class MyVideoSettings(BaseSettings):
    """
    Centralized settings for MyVideo application.

    All settings can be overridden via environment variables.
    Sensible defaults are provided for local development.
    """

    # Determine .env file location relative to this config file (project root)
    _env_file_path = Path(__file__).resolve().parent.parent / ".env"

    model_config = SettingsConfigDict(
        env_file=str(_env_file_path) if _env_file_path.exists() else ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"  # Allow extra env vars without error
    )

    # ==================== Project Paths ====================
    # Project root directory (detected automatically from this file's location)
    # Override via MYVIDEO_ROOT env var (useful for testing, containers)
    MYVIDEO_ROOT: Optional[str] = None

    @property
    def BASE_DIR(self) -> Path:
        """Project root directory."""
        if self.MYVIDEO_ROOT:
            return Path(self.MYVIDEO_ROOT)
        # This file is at /data/myvideo/app/config.py
        # Project root is /data/myvideo (parent.parent)
        return Path(__file__).resolve().parent.parent

    # ==================== Static Files Paths ====================
    @property
    def STATIC_DIR(self) -> Path:
        """Static files directory."""
        return self.BASE_DIR / "static"

    @property
    def UPLOADS_DIR(self) -> Path:
        """Video uploads directory."""
        return self.STATIC_DIR / "videos" / "uploads"

    @property
    def PROCESSED_DIR(self) -> Path:
        """Processed (transcoded) videos directory."""
        return self.STATIC_DIR / "videos" / "processed"

    @property
    def THUMBNAILS_DIR(self) -> Path:
        """Thumbnails directory."""
        return self.STATIC_DIR / "thumbnails"

    @property
    def THUMBNAILS_TEMP_DIR(self) -> Path:
        """Temporary thumbnails directory."""
        return self.THUMBNAILS_DIR / "temp"

    @property
    def AVATARS_DIR(self) -> Path:
        """User avatars directory."""
        return self.STATIC_DIR / "avatars"

    @property
    def DATA_DIR(self) -> Path:
        """Data directory (sensitive words, etc)."""
        return self.BASE_DIR / "data"

    # ==================== Static Files URLs ====================
    # These are URL paths served by the static files middleware
    THUMBNAILS_URL: str = "/static/thumbnails"
    VIDEOS_URL: str = "/static/videos"
    AVATARS_URL: str = "/static/avatars"

    # ==================== Database ====================
    DATABASE_HOST: str = "localhost"
    DATABASE_PORT: int = 5432
    DATABASE_USER: str = "myvideo"
    DATABASE_PASSWORD: str = "myvideo_password"
    DATABASE_NAME: str = "myvideo_db"

    @property
    def DATABASE_URL(self) -> str:
        """PostgreSQL connection URL."""
        return f"postgresql://{self.DATABASE_USER}:{self.DATABASE_PASSWORD}@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"

    DATABASE_ECHO: bool = True  # Log SQL statements (development)

    # ==================== Redis / Celery ====================
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None

    @property
    def REDIS_URL(self) -> str:
        """Redis connection URL."""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # ==================== Security / JWT ====================
    SECRET_KEY: str = "myvideo_secret_key_change_me_in_prod"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ==================== FFmpeg ====================
    FFMPEG_PATH: str = "ffmpeg"  # Assume in PATH by default
    FFPROBE_PATH: str = "ffprobe"

    # ==================== Application ====================
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_DEBUG: bool = False

    # ==================== CORS ====================
    CORS_ORIGINS: list[str] = ["*"]
    CORS_CREDENTIALS: bool = True
    CORS_METHODS: list[str] = ["*"]
    CORS_HEADERS: list[str] = ["*"]

    # ==================== Celery ====================
    CELERY_BROKER_URL: str = ""  # Falls back to REDIS_URL if empty
    CELERY_RESULT_BACKEND: str = ""  # Falls back to REDIS_URL if empty

    @property
    def CELERY_BROKER(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def CELERY_BACKEND(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL

    # ==================== Logging ====================
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Optional[str] = None  # Defaults to BASE_DIR / "server.log"

    @property
    def LOG_FILE_PATH(self) -> Optional[Path]:
        if self.LOG_FILE:
            return Path(self.LOG_FILE)
        return self.BASE_DIR / "server.log"

    # ==================== Sensitive Words ====================
    SENSITIVE_WORDS_FILE: Optional[str] = None

    @property
    def SENSITIVE_WORDS_PATH(self) -> Path:
        if self.SENSITIVE_WORDS_FILE:
            return Path(self.SENSITIVE_WORDS_FILE)
        return self.DATA_DIR / "sensitive_words.txt"

    # ==================== Helper Methods ====================

    def fs_path(self, url_path: str) -> Path:
        """
        Convert a URL path to a filesystem path.

        Args:
            url_path: URL path like "/static/thumbnails/image.jpg"

        Returns:
            Absolute filesystem path like "/data/myvideo/static/thumbnails/image.jpg"
        """
        # Remove leading slash and split
        parts = url_path.lstrip("/").split("/", 1)
        if len(parts) < 2:
            return self.BASE_DIR
        # parts[0] should be "static"
        if parts[0] == "static":
            return self.BASE_DIR / "static" / parts[1]
        return self.BASE_DIR / url_path.lstrip("/")

    def url_path(self, relative_path: str) -> str:
        """
        Convert a relative path to a URL path.

        Args:
            relative_path: Path like "thumbnails/image.jpg"

        Returns:
            URL path like "/static/thumbnails/image.jpg"
        """
        if relative_path.startswith("/"):
            return relative_path
        # Determine which static subdirectory
        if relative_path.startswith("thumbnails"):
            return f"{self.THUMBNAILS_URL}/{relative_path.replace('thumbnails/', '')}"
        elif relative_path.startswith("videos"):
            return f"{self.VIDEOS_URL}/{relative_path.replace('videos/', '')}"
        elif relative_path.startswith("avatars"):
            return f"{self.AVATARS_URL}/{relative_path.replace('avatars/', '')}"
        return f"/static/{relative_path}"

    def ensure_dirs(self) -> None:
        """Create all necessary directories."""
        dirs = [
            self.UPLOADS_DIR,
            self.PROCESSED_DIR,
            self.THUMBNAILS_DIR,
            self.THUMBNAILS_TEMP_DIR,
            self.AVATARS_DIR,
            self.DATA_DIR,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured directory exists: {d}")


@lru_cache()
def get_settings() -> MyVideoSettings:
    """Get cached settings instance."""
    return MyVideoSettings()


# Module-level singleton for convenience
# Usage: from app.config import settings
settings = get_settings()
