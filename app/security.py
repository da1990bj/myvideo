from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import ValidationError
from config import settings

# Security settings from centralized config
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码是否匹配"""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    return pwd_context.hash(password)

def get_token_expire_minutes() -> int:
    """从数据库获取token过期分钟数，支持运行时配置"""
    try:
        from sqlmodel import Session, select
        from database import engine
        from data_models import SystemConfig
        with Session(engine) as session:
            config = session.exec(select(SystemConfig).where(SystemConfig.key == "ACCESS_TOKEN_EXPIRE_MINUTES")).first()
            if config:
                return int(config.value)
    except Exception:
        pass
    return ACCESS_TOKEN_EXPIRE_MINUTES

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """生成 JWT 令牌"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=get_token_expire_minutes())

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


# ==================== 视频流签名 ====================

import hashlib
import hmac

def generate_video_token(video_id: str, expires_in_hours: int = 2) -> dict:
    """生成视频访问签名令牌"""
    import time
    expires = int(time.time()) + expires_in_hours * 3600
    data = f"{video_id}:{expires}"
    signature = hmac.new(
        SECRET_KEY.encode(),
        data.encode(),
        hashlib.sha256
    ).hexdigest()
    return {
        "token": signature,
        "expires": expires,
        "video_id": video_id
    }

def verify_video_token(video_id: str, token: str, expires: int) -> bool:
    """验证视频访问令牌"""
    import time
    if int(time.time()) > expires:
        return False  # 已过期
    data = f"{video_id}:{expires}"
    expected = hmac.new(
        SECRET_KEY.encode(),
        data.encode(),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(token, expected)
