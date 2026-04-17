"""
认证相关路由
"""
from typing import List, Annotated, Optional
from uuid import UUID
from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select, func

from database import get_session
from data_models import User, UserCreate, UserRead, UserUpdate, UserLogin, Token, TokenResponse, RefreshRequest, Video, UserRole, Role
from security import get_password_hash, verify_password, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES, create_refresh_token, verify_refresh_token, revoke_refresh_token
from dependencies import get_current_user, get_current_user_optional

router = APIRouter(prefix="", tags=["认证"])


@router.post("/token", response_model=TokenResponse)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: Session = Depends(get_session)
):
    """
    登录获取 JWT token

    OAuth2 兼容的登录接口，同时返回 access_token 和 refresh_token
    """
    user = session.exec(select(User).where(User.username == form_data.username)).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    access_token = create_access_token(data={"sub": user.username})
    refresh_token = create_refresh_token(user.id, device_info=None, session=session)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, token_type="bearer")


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh_access_token(
    refresh_data: RefreshRequest,
    session: Session = Depends(get_session)
):
    """
    使用 refresh_token 获取新的 access_token
    """
    user_id = verify_refresh_token(refresh_data.refresh_token, session)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token"
        )

    user = session.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    access_token = create_access_token(data={"sub": user.username})
    new_refresh_token = create_refresh_token(user.id, device_info=None, session=session)
    return TokenResponse(access_token=access_token, refresh_token=new_refresh_token, token_type="bearer")


@router.post("/auth/logout")
async def logout(
    refresh_data: RefreshRequest,
    session: Session = Depends(get_session)
):
    """
    撤销 refresh_token (登出)
    """
    revoked = revoke_refresh_token(refresh_data.refresh_token, session)
    if not revoked:
        raise HTTPException(status_code=404, detail="Refresh token not found")
    return {"message": "Logged out successfully"}


@router.post("/users/register", response_model=UserRead, status_code=201)
async def register(
    user_data: UserCreate,
    session: Session = Depends(get_session)
):
    """
    注册新用户
    """
    # 检查用户名是否已存在
    existing = session.exec(select(User).where(User.username == user_data.username)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already registered")

    # 检查邮箱是否已存在
    if user_data.email:
        existing_email = session.exec(select(User).where(User.email == user_data.email)).first()
        if existing_email:
            raise HTTPException(status_code=400, detail="Email already registered")

    # 创建用户
    hashed_password = get_password_hash(user_data.password)
    db_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hashed_password,
    )
    session.add(db_user)
    session.commit()
    session.refresh(db_user)

    return db_user


@router.get("/users/me", response_model=UserRead)
async def get_me(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取当前登录用户信息
    """
    from sqlmodel import select
    user_roles = session.exec(select(UserRole).where(UserRole.user_id == current_user.id)).all()
    role_ids = [ur.role_id for ur in user_roles]
    role_names = []
    for rid in role_ids:
        role = session.get(Role, rid)
        if role:
            role_names.append(role.name)

    return UserRead(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        is_active=current_user.is_active,
        is_admin=current_user.is_admin,
        is_vip=current_user.is_vip,
        role_ids=role_ids,
        role_names=role_names,
        created_at=current_user.created_at,
        avatar_path=current_user.avatar_path,
        bio=current_user.bio,
        has_private_videos_password=bool(current_user.private_videos_password_hash)
    )


@router.put("/users/me", response_model=UserRead)
async def update_me(
    data: UserUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    更新当前登录用户信息
    """
    if data.bio is not None:
        current_user.bio = data.bio if data.bio != "" else None
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return current_user


@router.put("/users/me/password")
async def change_password(
    old_password: str = Body(...),
    new_password: str = Body(...),
    current_user: User = Depends(get_current_user)
):
    """
    修改当前用户密码
    """
    from security import verify_password

    if not verify_password(old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="旧密码错误")

    from security import get_password_hash
    current_user.hashed_password = get_password_hash(new_password)
    return {"message": "密码修改成功"}


@router.put("/users/me/private-videos-password")
async def set_private_videos_password(
    password: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    设置私密视频区域密码
    """
    from security import get_password_hash
    if password:
        current_user.private_videos_password_hash = get_password_hash(password)
    else:
        current_user.private_videos_password_hash = None
    session.add(current_user)
    session.commit()
    return {"message": "私密视频密码已设置" if password else "私密视频密码已清除"}


@router.post("/users/me/verify-private-password")
async def verify_private_videos_password(
    password: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user)
):
    """
    验证私密视频区域密码，返回临时令牌
    """
    from security import verify_password, create_access_token
    import uuid
    if not current_user.private_videos_password_hash:
        raise HTTPException(status_code=400, detail="未设置私密视频密码")
    if not verify_password(password, current_user.private_videos_password_hash):
        raise HTTPException(status_code=401, detail="密码错误")
    # 生成临时令牌，有效期24小时
    from datetime import timedelta
    temp_token = create_access_token({"sub": str(current_user.id) + ":private"}, expires_delta=timedelta(seconds=86400))
    return {"temp_token": temp_token}


@router.get("/users/{user_id}/profile")
async def get_user_profile(
    user_id: str,
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: Session = Depends(get_session)
):
    """
    获取指定用户的公开资料
    """
    # 先尝试按用户名查询
    user = session.exec(select(User).where(User.username == user_id)).first()
    if not user:
        # 尝试按 UUID 查询
        try:
            user = session.get(User, user_id)
        except Exception:
            user = None

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 统计信息
    videos = session.exec(select(Video).where(Video.user_id == user.id, Video.is_approved == "approved")).all()
    video_count = len(videos)
    like_count = sum(v.like_count for v in videos)
    view_count = sum(v.views for v in videos)

    # 粉丝数和关注数
    from data_models import UserFollow
    followers_count = session.exec(select(UserFollow).where(UserFollow.followed_id == user.id)).all()
    following_count = session.exec(select(UserFollow).where(UserFollow.follower_id == user.id)).all()

    # 是否是自己以及是否已关注（仅登录用户可见）
    is_self = current_user.id == user.id if current_user else False
    is_following = False
    is_blocked_by = False
    if current_user and not is_self:
        is_following = session.exec(
            select(UserFollow).where(
                UserFollow.follower_id == current_user.id,
                UserFollow.followed_id == user.id
            )
        ).first() is not None

        # 检查是否被对方拉黑
        from data_models import UserBlock
        is_blocked_by = session.exec(
            select(UserBlock).where(
                UserBlock.blocker_id == user.id,
                UserBlock.blocked_id == current_user.id
            )
        ).first() is not None

    return {
        "id": str(user.id),
        "username": user.username,
        "display_name": user.username,
        "avatar_path": user.avatar_path,
        "bio": user.bio,
        "created_at": user.created_at,
        "videos_count": video_count,
        "like_count": like_count,
        "view_count": view_count,
        "followers_count": len(followers_count),
        "following_count": len(following_count),
        "is_self": is_self,
        "is_following": is_following,
        "is_blocked_by": is_blocked_by,
    }


@router.get("/users/{user_id}/videos/public")
async def get_user_public_videos(
    user_id: str,
    session: Session = Depends(get_session),
    page: int = 1,
    size: int = 20
):
    """
    获取用户公开视频列表
    """
    # 先尝试按用户名查询
    user = session.exec(select(User).where(User.username == user_id)).first()
    if not user:
        try:
            user = session.get(User, user_id)
        except Exception:
            user = None

    if not user:
        return []

    offset = (page - 1) * size
    videos = session.exec(
        select(Video)
        .where(
            Video.user_id == user.id,
            Video.is_approved == "approved",
            Video.status == "completed",
            Video.visibility == "public",
            Video.is_deleted == False
        )
        .order_by(Video.created_at.desc())
        .offset(offset)
        .limit(size)
    ).all()

    # 手动构建响应，确保 owner 和 category 信息完整
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
                "slug": v.category.slug,
            }
        result.append(video_dict)

    return result


@router.get("/users/me/videos/private")
async def get_my_private_videos(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    page: int = 1,
    size: int = 20
):
    """
    获取当前用户的私密视频
    """
    offset = (page - 1) * size
    videos = session.exec(
        select(Video)
        .where(Video.user_id == current_user.id)
        .where(Video.is_deleted == False)
        .where(Video.visibility == "private")
        .order_by(Video.created_at.desc())
        .offset(offset)
        .limit(size)
    ).all()

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
                "slug": v.category.slug,
            }
        result.append(video_dict)

    return result


@router.get("/users/me/stats")
async def get_my_stats(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取当前用户的统计数据
    """
    videos = session.exec(select(Video).where(Video.user_id == current_user.id, Video.is_deleted == False)).all()

    total_views = sum(v.views or 0 for v in videos)
    total_likes = sum(v.like_count or 0 for v in videos)
    total_comments = 0  # 简化计算
    completed_videos = len([v for v in videos if v.status == "completed"])

    # 用户获藏的视频总数（收藏 on 用户的视频）
    from data_models import VideoFavorite
    user_video_ids = select(Video.id).where(Video.user_id == current_user.id)
    total_favorites = session.exec(
        select(VideoFavorite).where(VideoFavorite.video_id.in_(user_video_ids))
    ).all()

    return {
        "total_videos": len(videos),
        "completed_videos": completed_videos,
        "total_views": total_views,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_favorites": len(total_favorites),
    }


@router.get("/users/me/videos")
async def get_my_videos(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    page: int = 1,
    size: int = 20,
    category: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None
):
    """
    获取当前用户的所有视频（包括创作中的）
    """
    offset = (page - 1) * size

    # 构建查询
    query = select(Video).where(Video.user_id == current_user.id).where(Video.is_deleted == False)

    if category:
        query = query.where(Video.category_id == int(category))
    if status:
        query = query.where(Video.status == status)
    if keyword:
        query = query.where(Video.title.ilike(f"%{keyword}%"))

    # 查询总数
    count_query = select(func.count(Video.id)).where(Video.user_id == current_user.id).where(Video.is_deleted == False)
    if category:
        count_query = count_query.where(Video.category_id == int(category))
    if status:
        count_query = count_query.where(Video.status == status)
    if keyword:
        count_query = count_query.where(Video.title.ilike(f"%{keyword}%"))
    total = session.exec(count_query).one()

    videos = session.exec(
        query.order_by(Video.created_at.desc())
        .offset(offset)
        .limit(size)
    ).all()

    # 手动构建响应，确保 owner 和 category 信息完整
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
                "slug": v.category.slug,
            }
        result.append(video_dict)

    return {"videos": result, "total": total, "page": page, "size": size}
