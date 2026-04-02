"""
认证相关路由
"""
from typing import List, Annotated, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select

from database import get_session
from data_models import User, UserCreate, UserRead, UserLogin, Token, Video
from security import get_password_hash, verify_password, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES
from dependencies import get_current_user, get_current_user_optional

router = APIRouter(prefix="", tags=["认证"])


@router.post("/token", response_model=Token)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: Session = Depends(get_session)
):
    """
    登录获取 JWT token

    OAuth2 兼容的登录接口
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
    return Token(access_token=access_token, token_type="bearer")


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
async def get_me(current_user: User = Depends(get_current_user)):
    """
    获取当前登录用户信息
    """
    return current_user


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
    videos = session.exec(select(Video).where(Video.user_id == user.id, Video.status.in_(["completed", "approved"]))).all()
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
    if current_user and not is_self:
        is_following = session.exec(
            select(UserFollow).where(
                UserFollow.follower_id == current_user.id,
                UserFollow.followed_id == user.id
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
        .where(Video.user_id == user.id, Video.status.in_(["completed", "approved"]))
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


@router.get("/users/me/stats")
async def get_my_stats(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取当前用户的统计数据
    """
    videos = session.exec(select(Video).where(Video.user_id == current_user.id)).all()

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
    size: int = 20
):
    """
    获取当前用户的所有视频（包括创作中的）
    """
    offset = (page - 1) * size
    videos = session.exec(
        select(Video)
        .where(Video.user_id == current_user.id)
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
