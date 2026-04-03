"""
共享依赖模块 - 提供跨路由模块复用的工具函数和类
"""
from typing import Annotated, List, Optional
from uuid import UUID
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlmodel import Session, select

from database import get_session
from data_models import User, Role, UserRole, AdminLog, Tag, VideoTag
from security import SECRET_KEY, ALGORITHM

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
    session: Session = Depends(get_session)
) -> User:
    """获取当前登录用户（必须登录）"""
    if not token:
        raise credentials_exception
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = session.exec(select(User).where(User.username == username)).first()
    if user is None:
        raise credentials_exception
    return user


async def get_current_user_optional(
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
    session: Session = Depends(get_session)
) -> Optional[User]:
    """获取当前用户（可选，未登录返回 None）"""
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
        user = session.exec(select(User).where(User.username == username)).first()
        return user
    except JWTError:
        return None


async def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """获取当前管理员用户"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user


class PermissionChecker:
    """权限检查器依赖 - 支持多角色"""

    def __init__(self, permission: str):
        self.permission = permission

    def __call__(self, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> User:
        if not user.is_active:
            raise HTTPException(status_code=400, detail="Inactive user")

        # 获取用户所有角色
        user_roles = session.exec(select(UserRole).where(UserRole.user_id == user.id)).all()
        if not user_roles:
            raise HTTPException(status_code=403, detail="Role not assigned")

        # 获取所有角色对象
        role_ids = [ur.role_id for ur in user_roles]
        roles = session.exec(select(Role).where(Role.id.in_(role_ids))).all()

        allowed = False
        for role in roles:
            if role.permissions == "*":
                # 任何角色有 * 权限即代表超级管理员
                allowed = True
                break
            perms = [p.strip() for p in role.permissions.split(",") if p.strip()]
            if self.permission in perms:
                allowed = True
                break

        if not allowed:
            raise HTTPException(status_code=403, detail=f"Permission '{self.permission}' required")

        return user


def log_admin_action(
    session: Session,
    admin_id: UUID,
    action: str,
    target_id: str = None,
    details: str = None,
    ip_address: str = None
):
    """记录管理员操作日志"""
    log = AdminLog(admin_id=admin_id, action=action, target_id=target_id, details=details, ip_address=ip_address)
    session.add(log)


def check_permission(user: User, permission: str, session: Session) -> bool:
    """检查用户是否有指定权限"""
    if user.is_admin:
        if user.role_id:
            role = session.get(Role, user.role_id)
            if role:
                if role.permissions == "*":
                    return True
                perms = role.permissions.split(",")
                return permission in perms
    return False


def process_tags(session: Session, video, tag_list: List[str]):
    """
    处理视频标签关联

    Args:
        session: 数据库会话
        video: Video 对象
        tag_list: 标签名称列表
    """
    # 清除现有标签关联
    current_tags = session.exec(select(VideoTag).where(VideoTag.video_id == video.id)).all()
    for tag_link in current_tags:
        session.delete(tag_link)
        tag = session.get(Tag, tag_link.tag_id)
        if tag:
            tag.usage_count = max(0, tag.usage_count - 1)
            session.add(tag)

    # 添加新标签
    for tag_name in tag_list:
        if not tag_name:
            continue
        tag_name = tag_name.strip()

        tag = session.exec(select(Tag).where(Tag.name == tag_name)).first()
        if not tag:
            tag = Tag(name=tag_name, usage_count=0)
            session.add(tag)
            session.commit()
            session.refresh(tag)

        link = VideoTag(video_id=video.id, tag_id=tag.id)
        session.add(link)

        tag.usage_count += 1
        session.add(tag)
