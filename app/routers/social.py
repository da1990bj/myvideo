"""
社交相关路由 - 关注、粉丝、通知、收藏
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from database import get_session
from data_models import (
    User, UserFollow, UserBlock, UserRead,
    Video, VideoRead, VideoFavorite, Collection, CollectionRead, CollectionFavorite, Notification
)
from dependencies import get_current_user, get_current_user_optional

router = APIRouter(prefix="", tags=["社交"])


# ==================== 关注/粉丝 ====================

@router.post("/users/{user_id}/follow")
async def follow_user(
    user_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    关注用户
    """
    if str(current_user.id) == user_id:
        raise HTTPException(status_code=400, detail="Cannot follow yourself")

    # 先尝试按用户名查询
    target_user = session.exec(select(User).where(User.username == user_id)).first()
    if not target_user:
        try:
            target_user = session.get(User, user_id)
        except Exception:
            target_user = None

    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # 检查是否已关注
    existing = session.exec(
        select(UserFollow).where(
            UserFollow.follower_id == current_user.id,
            UserFollow.followed_id == user_id
        )
    ).first()

    if existing:
        return {"message": "Already following", "following": True}

    follow = UserFollow(
        follower_id=current_user.id,
        followed_id=user_id
    )
    session.add(follow)

    # 发送通知
    if target_user.id != current_user.id:
        notif = Notification(
            sender_id=current_user.id,
            recipient_id=target_user.id,
            type="follow",
            entity_id=str(current_user.id)
        )
        session.add(notif)

    session.commit()

    return {"message": "Following", "following": True}


@router.delete("/users/{user_id}/follow")
async def unfollow_user(
    user_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    取消关注（取消自己对他人的关注）
    """
    # 先尝试按用户名查询
    target_user = session.exec(select(User).where(User.username == user_id)).first()
    if not target_user:
        try:
            target_user = session.get(User, user_id)
        except Exception:
            target_user = None

    if not target_user:
        return {"message": "Unfollowed", "following": False}

    existing = session.exec(
        select(UserFollow).where(
            UserFollow.follower_id == current_user.id,
            UserFollow.followed_id == target_user.id
        )
    ).first()

    if existing:
        session.delete(existing)
        session.commit()

    return {"message": "Unfollowed", "following": False}


@router.delete("/users/{user_id}/follower")
async def remove_follower(
    user_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    移除粉丝（删除他人对自己的关注）
    """
    # 先尝试按用户名查询
    target_user = session.exec(select(User).where(User.username == user_id)).first()
    if not target_user:
        try:
            target_user = session.get(User, user_id)
        except Exception:
            target_user = None

    if not target_user:
        return {"message": "Follower removed", "removed": False}

    # 删除对方对当前用户的关注
    existing = session.exec(
        select(UserFollow).where(
            UserFollow.follower_id == target_user.id,
            UserFollow.followed_id == current_user.id
        )
    ).first()

    if existing:
        session.delete(existing)
        session.commit()

    return {"message": "Follower removed", "removed": True}


@router.get("/users/me/following", response_model=List[UserRead])
async def get_following(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取当前用户关注的人
    """
    follows = session.exec(
        select(UserFollow).where(UserFollow.follower_id == current_user.id)
    ).all()

    following_ids = [f.followed_id for f in follows]
    if not following_ids:
        return []

    users = session.exec(select(User).where(User.id.in_(following_ids))).all()
    return users


@router.get("/users/me/followers", response_model=List[UserRead])
async def get_followers(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取当前用户的粉丝列表
    """
    follows = session.exec(
        select(UserFollow).where(UserFollow.followed_id == current_user.id)
    ).all()

    follower_ids = [f.follower_id for f in follows]
    if not follower_ids:
        return []

    users = session.exec(select(User).where(User.id.in_(follower_ids))).all()
    return users


@router.get("/users/me/following", response_model=List[UserRead])
async def get_following(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取当前用户关注的人
    """
    follows = session.exec(
        select(UserFollow).where(UserFollow.follower_id == current_user.id)
    ).all()

    following_ids = [f.followed_id for f in follows]
    if not following_ids:
        return []

    users = session.exec(select(User).where(User.id.in_(following_ids))).all()
    return users


@router.get("/users/me/blocks", response_model=List[UserRead])
async def get_blocks(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取拉黑的用户列表
    """
    blocks = session.exec(
        select(UserBlock).where(UserBlock.blocker_id == current_user.id)
    ).all()

    blocked_ids = [b.blocked_id for b in blocks]
    if not blocked_ids:
        return []

    users = session.exec(select(User).where(User.id.in_(blocked_ids))).all()
    return users


@router.post("/users/{user_id}/block")
async def block_user(
    user_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    拉黑用户
    """
    if str(current_user.id) == user_id:
        raise HTTPException(status_code=400, detail="Cannot block yourself")

    target_user = session.exec(select(User).where(User.username == user_id)).first()
    if not target_user:
        try:
            target_user = session.get(User, user_id)
        except Exception:
            target_user = None

    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # 检查是否已拉黑
    existing = session.exec(
        select(UserBlock).where(
            UserBlock.blocker_id == current_user.id,
            UserBlock.blocked_id == user_id
        )
    ).first()

    if existing:
        return {"message": "Already blocked", "blocked": True}

    block = UserBlock(
        blocker_id=current_user.id,
        blocked_id=user_id
    )
    session.add(block)

    # 同时取消关注（如果有关注关系）
    follow = session.exec(
        select(UserFollow).where(
            UserFollow.follower_id == current_user.id,
            UserFollow.followed_id == user_id
        )
    ).first()
    if follow:
        session.delete(follow)

    # 取消被关注（如果对方关注了自己）
    follower = session.exec(
        select(UserFollow).where(
            UserFollow.follower_id == user_id,
            UserFollow.followed_id == current_user.id
        )
    ).first()
    if follower:
        session.delete(follower)

    session.commit()

    return {"message": "Blocked", "blocked": True}


@router.delete("/users/{user_id}/block")
async def unblock_user(
    user_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    解除拉黑
    """
    target_user = session.exec(select(User).where(User.username == user_id)).first()
    if not target_user:
        try:
            target_user = session.get(User, user_id)
        except Exception:
            target_user = None

    if not target_user:
        return {"message": "Unblocked", "blocked": False}

    existing = session.exec(
        select(UserBlock).where(
            UserBlock.blocker_id == current_user.id,
            UserBlock.blocked_id == target_user.id
        )
    ).first()

    if existing:
        session.delete(existing)
        session.commit()

    return {"message": "Unblocked", "blocked": False}


# ==================== 收藏夹 ====================

@router.post("/videos/{video_id}/favorite")
async def favorite_video(
    video_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    收藏视频
    """
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    existing = session.exec(
        select(VideoFavorite).where(
            VideoFavorite.user_id == current_user.id,
            VideoFavorite.video_id == video_id
        )
    ).first()

    if existing:
        return {"message": "Already favorited", "favorited": True}

    fav = VideoFavorite(
        user_id=current_user.id,
        video_id=video_id
    )
    session.add(fav)
    session.commit()

    return {"message": "Favorited", "favorited": True}


@router.post("/collections/{collection_id}/favorite")
async def favorite_collection(
    collection_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    收藏合集
    """
    collection = session.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    existing = session.exec(
        select(CollectionFavorite).where(
            CollectionFavorite.user_id == current_user.id,
            CollectionFavorite.collection_id == collection_id
        )
    ).first()

    if existing:
        return {"message": "Already favorited", "favorited": True}

    fav = CollectionFavorite(
        user_id=current_user.id,
        collection_id=collection_id
    )
    session.add(fav)
    session.commit()

    return {"message": "Favorited", "favorited": True}


@router.get("/users/me/favorites/videos", response_model=List[VideoRead])
async def get_favorite_videos(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取收藏的视频列表
    """
    favorites = session.exec(
        select(VideoFavorite).where(VideoFavorite.user_id == current_user.id)
    ).all()

    video_ids = [f.video_id for f in favorites]
    if not video_ids:
        return []

    videos = session.exec(select(Video).where(Video.id.in_(video_ids))).all()
    return videos


@router.get("/users/me/favorites/collections", response_model=List[CollectionRead])
async def get_favorite_collections(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取收藏的合集列表
    """
    favorites = session.exec(
        select(CollectionFavorite).where(CollectionFavorite.user_id == current_user.id)
    ).all()

    collection_ids = [f.collection_id for f in favorites]
    if not collection_ids:
        return []

    collections = session.exec(select(Collection).where(Collection.id.in_(collection_ids))).all()
    return collections


@router.get("/users/me/liked/videos", response_model=List[VideoRead])
async def get_liked_videos(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取点赞的视频列表
    """
    from data_models import VideoLike

    likes = session.exec(
        select(VideoLike).where(
            VideoLike.user_id == current_user.id,
            VideoLike.like_type == "like"
        )
    ).all()

    video_ids = [l.video_id for l in likes]
    if not video_ids:
        return []

    videos = session.exec(select(Video).where(Video.id.in_(video_ids))).all()
    return videos


# ==================== 通知 ====================

@router.get("/notifications")
async def get_notifications(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取通知列表
    """
    notifications = session.exec(
        select(Notification)
        .where(Notification.recipient_id == str(current_user.id))
        .order_by(Notification.created_at.desc())
        .limit(50)
    ).all()

    result = []
    for n in notifications:
        notif_dict = n.model_dump()
        sender = session.get(User, n.sender_id) if n.sender_id else None
        notif_dict["sender"] = {
            "id": str(sender.id),
            "username": sender.username,
            "display_name": sender.username,
            "avatar_path": sender.avatar_path
        } if sender else None
        result.append(notif_dict)

    return result


@router.get("/notifications/unread-count")
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    获取未读通知数量
    """
    count = session.exec(
        select(Notification).where(
            Notification.recipient_id == str(current_user.id),
            Notification.is_read == False
        )
    ).all()

    return {"count": len(count)}


@router.post("/notifications/read-all")
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    标记所有通知为已读
    """
    notifications = session.exec(
        select(Notification).where(
            Notification.recipient_id == str(current_user.id),
            Notification.is_read == False
        )
    ).all()

    for n in notifications:
        n.is_read = True
        session.add(n)

    session.commit()

    return {"message": "All notifications marked as read"}


@router.post("/notifications/{notif_id}/read")
async def mark_read(
    notif_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    标记单条通知为已读
    """
    notif = session.get(Notification, notif_id)
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")

    if str(notif.recipient_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")

    notif.is_read = True
    session.add(notif)
    session.commit()

    return {"message": "Notification marked as read"}
