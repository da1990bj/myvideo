"""
存储抽象层 - 支持本地存储和云存储（S3/OSS）

提供统一的文件存储接口，便于未来扩展到云存储服务。
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, BinaryIO
import logging
import shutil

from config import settings

logger = logging.getLogger(__name__)


class StorageBackend(ABC):
    """
    存储后端抽象基类

    所有存储实现必须继承此类并实现以下方法：
    - save / save_file
    - load / load_file
    - delete
    - exists
    - get_url
    """

    @abstractmethod
    def save(self, path: Path, data: bytes) -> bool:
        """
        保存文件数据

        Args:
            path: 文件路径
            data: 文件数据

        Returns:
            是否保存成功
        """
        pass

    @abstractmethod
    def save_file(self, src: Path, dst: Path) -> bool:
        """
        保存本地文件

        Args:
            src: 源文件路径
            dst: 目标文件路径

        Returns:
            是否保存成功
        """
        pass

    @abstractmethod
    def load(self, path: Path) -> Optional[bytes]:
        """
        加载文件数据

        Args:
            path: 文件路径

        Returns:
            文件数据，失败返回 None
        """
        pass

    @abstractmethod
    def load_file(self, path: Path, dst: Path) -> bool:
        """
        加载文件到本地路径

        Args:
            path: 源文件路径
            dst: 目标路径

        Returns:
            是否加载成功
        """
        pass

    @abstractmethod
    def delete(self, path: Path) -> bool:
        """
        删除文件

        Args:
            path: 文件路径

        Returns:
            是否删除成功
        """
        pass

    @abstractmethod
    def exists(self, path: Path) -> bool:
        """
        检查文件是否存在

        Args:
            path: 文件路径

        Returns:
            是否存在
        """
        pass

    @abstractmethod
    def get_url(self, path: Path) -> str:
        """
        获取文件的访问 URL

        Args:
            path: 文件路径

        Returns:
            访问 URL
        """
        pass


class LocalStorage(StorageBackend):
    """
    本地文件系统存储

    适用于单节点部署或通过 NFS 等共享存储挂载的场景
    """

    def __init__(self, base_dir: Optional[Path] = None):
        """
        初始化本地存储

        Args:
            base_dir: 存储根目录，默认为 settings.BASE_DIR
        """
        self.base_dir = base_dir or settings.BASE_DIR

    def _resolve_path(self, path: Path) -> Path:
        """解析相对路径为绝对路径"""
        if path.is_absolute():
            return path
        return self.base_dir / path

    def save(self, path: Path, data: bytes) -> bool:
        try:
            full_path = self._resolve_path(path)
            full_path.parent.mkdir(parents=True, exist_ok=True)
            with open(full_path, 'wb') as f:
                f.write(data)
            logger.debug(f"Saved file: {full_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save file {path}: {e}")
            return False

    def save_file(self, src: Path, dst: Path) -> bool:
        try:
            full_dst = self._resolve_path(dst)
            full_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, full_dst)
            logger.debug(f"Copied file: {src} -> {full_dst}")
            return True
        except Exception as e:
            logger.error(f"Failed to copy file {src} to {dst}: {e}")
            return False

    def load(self, path: Path) -> Optional[bytes]:
        try:
            full_path = self._resolve_path(path)
            with open(full_path, 'rb') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to load file {path}: {e}")
            return None

    def load_file(self, path: Path, dst: Path) -> bool:
        try:
            full_path = self._resolve_path(path)
            shutil.copy2(full_path, dst)
            return True
        except Exception as e:
            logger.error(f"Failed to load file {path} to {dst}: {e}")
            return False

    def delete(self, path: Path) -> bool:
        try:
            full_path = self._resolve_path(path)
            if full_path.exists():
                full_path.unlink()
                logger.debug(f"Deleted file: {full_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete file {path}: {e}")
            return False

    def exists(self, path: Path) -> bool:
        return self._resolve_path(path).exists()

    def get_url(self, path: Path) -> str:
        """
        将本地路径转换为 URL 路径

        例如: /data/myvideo/static/thumbnails/foo.jpg -> /static/thumbnails/foo.jpg
        """
        full_path = self._resolve_path(path)
        try:
            relative = full_path.relative_to(self.base_dir)
            return f"/{relative.as_posix()}"
        except ValueError:
            # 如果不在 base_dir 内，返回绝对路径
            return str(full_path)


class S3Storage(StorageBackend):
    """
    S3 兼容对象存储 (AWS S3, MinIO, 阿里云 OSS 等)

    需要安装 boto3: pip install boto3
    """

    def __init__(
        self,
        endpoint_url: str,
        aws_access_key_id: str,
        aws_secret_access_key: str,
        bucket_name: str,
        region_name: str = "us-east-1",
        prefix: str = ""
    ):
        """
        初始化 S3 存储

        Args:
            endpoint_url: S3 端点 URL
            aws_access_key_id: Access Key ID
            aws_secret_access_key: Secret Access Key
            bucket_name: Bucket 名称
            region_name: 区域
            prefix: 文件路径前缀
        """
        try:
            import boto3
        except ImportError:
            raise ImportError("boto3 is required for S3 storage. Install it with: pip install boto3")

        self.s3 = boto3.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region_name
        )
        self.bucket_name = bucket_name
        self.prefix = prefix

    def _get_key(self, path: Path) -> str:
        """获取 S3 key"""
        path_str = path.as_posix() if isinstance(path, Path) else str(path)
        if self.prefix:
            return f"{self.prefix}/{path_str}"
        return path_str

    def save(self, path: Path, data: bytes) -> bool:
        try:
            key = self._get_key(path)
            self.s3.put_object(Bucket=self.bucket_name, Key=key, Body=data)
            logger.debug(f"Saved to S3: {key}")
            return True
        except Exception as e:
            logger.error(f"Failed to save to S3 {path}: {e}")
            return False

    def save_file(self, src: Path, dst: Path) -> bool:
        try:
            key = self._get_key(dst)
            self.s3.upload_file(str(src), self.bucket_name, key)
            logger.debug(f"Uploaded to S3: {src} -> {key}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload to S3 {src} to {dst}: {e}")
            return False

    def load(self, path: Path) -> Optional[bytes]:
        try:
            key = self._get_key(path)
            response = self.s3.get_object(Bucket=self.bucket_name, Key=key)
            return response['Body'].read()
        except Exception as e:
            logger.error(f"Failed to load from S3 {path}: {e}")
            return None

    def load_file(self, path: Path, dst: Path) -> bool:
        try:
            key = self._get_key(path)
            self.s3.download_file(self.bucket_name, key, str(dst))
            return True
        except Exception as e:
            logger.error(f"Failed to download from S3 {path} to {dst}: {e}")
            return False

    def delete(self, path: Path) -> bool:
        try:
            key = self._get_key(path)
            self.s3.delete_object(Bucket=self.bucket_name, Key=key)
            logger.debug(f"Deleted from S3: {key}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete from S3 {path}: {e}")
            return False

    def exists(self, path: Path) -> bool:
        try:
            key = self._get_key(path)
            self.s3.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except Exception:
            return False

    def get_url(self, path: Path) -> str:
        """获取 S3 对象的预签名 URL（默认 1 小时有效期）"""
        try:
            key = self._get_key(path)
            url = self.s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': key},
                ExpiresIn=3600
            )
            return url
        except Exception as e:
            logger.error(f"Failed to generate URL for S3 {path}: {e}")
            return ""


# ==================== 存储实例工厂 ====================

_storage_backend: Optional[StorageBackend] = None


def get_storage() -> StorageBackend:
    """
    获取存储后端实例（单例模式）

    根据配置返回对应的存储后端实现
    """
    global _storage_backend

    if _storage_backend is not None:
        return _storage_backend

    # 检查配置决定使用哪种存储
    storage_type = getattr(settings, 'STORAGE_BACKEND', 'local').lower()

    if storage_type == 's3':
        _storage_backend = S3Storage(
            endpoint_url=getattr(settings, 'S3_ENDPOINT_URL', ''),
            aws_access_key_id=getattr(settings, 'S3_ACCESS_KEY_ID', ''),
            aws_secret_access_key=getattr(settings, 'S3_SECRET_ACCESS_KEY', ''),
            bucket_name=getattr(settings, 'S3_BUCKET_NAME', ''),
            region_name=getattr(settings, 'S3_REGION_NAME', 'us-east-1'),
            prefix=getattr(settings, 'S3_PREFIX', '')
        )
        logger.info("Storage backend: S3")
    elif storage_type == 'oss':
        # 阿里云 OSS 使用 S3 兼容接口
        oss_endpoint = getattr(settings, 'OSS_ENDPOINT', '')
        _storage_backend = S3Storage(
            endpoint_url=f"https://{oss_endpoint}",
            aws_access_key_id=getattr(settings, 'OSS_ACCESS_KEY_ID', ''),
            aws_secret_access_key=getattr(settings, 'OSS_SECRET_ACCESS_KEY', ''),
            bucket_name=getattr(settings, 'OSS_BUCKET_NAME', ''),
            region_name=getattr(settings, 'OSS_REGION_NAME', 'cn-hangzhou'),
            prefix=getattr(settings, 'OSS_PREFIX', '')
        )
        logger.info("Storage backend: OSS (S3-compatible)")
    else:
        _storage_backend = LocalStorage()
        logger.info("Storage backend: Local")

    return _storage_backend


def reset_storage():
    """重置存储实例（用于测试或配置更改后重新初始化）"""
    global _storage_backend
    _storage_backend = None
