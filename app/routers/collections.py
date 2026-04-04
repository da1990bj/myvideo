"""
合集/收藏夹路由
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlmodel import Session, select

from database import get_session
from data_models import Collection, CollectionRead, CollectionCreate, CollectionUpdate, CollectionItem, Video, User, Role, UserRole
from dependencies import get_current_user, get_current_user_optional, PermissionChecker

router = APIRouter(prefix="", tags=["合集"])


@router.post("/collections", response_model=CollectionRead)
async def create_collection(
    collection_data: CollectionCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    创建新合集
    """
    collection = Collection(
        title=collection_data.title,
        description=collection_data.description,
        user_id=current_user.id,
        is_public=collection_data.is_public,
    )
    session.add(collection)
    session.commit()
    session.refresh(collection)

    return collection


@router.put("/collections/{collection_id}", response_model=CollectionRead)
async def update_collection(
    collection_id: UUID,
    update_data: CollectionUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    更新合集信息
    """
    collection = session.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    if collection.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    if update_data.title is not None:
        collection.title = update_data.title
    if update_data.description is not None:
        collection.description = update_data.description

    session.add(collection)
    session.commit()
    session.refresh(collection)

    return collection


@router.get("/collections", response_model=List[CollectionRead])
async def get_collections(
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: Session = Depends(get_session),
    user_id: Optional[str] = None,
    page: int = 1,
    size: int = 20
):
    """
    获取合集列表（可选：user_id过滤）
    """
    offset = (page - 1) * size

    # 如果没有 user_id 参数且没有登录，返回空
    if not user_id and not current_user:
        return []

    # 确定查询哪个用户的合集
    if user_id:
        # 可能是用户名，需要解析为 UUID
        user = session.exec(select(User).where(User.username == user_id)).first()
        if not user:
            try:
                user = session.get(User, user_id)
            except Exception:
                return []
        target_user_id = user.id if user else None
    else:
        target_user_id = current_user.id

    if not target_user_id:
        return []

    collections = session.exec(
        select(Collection)
        .where(Collection.user_id == target_user_id)
        .order_by(Collection.created_at.desc())
        .offset(offset)
        .limit(size)
    ).all()

    # 动态计算 video_count 和 first_video_id
    result = []
    for coll in collections:
        items = session.exec(
            select(CollectionItem)
            .where(CollectionItem.collection_id == coll.id)
            .order_by(CollectionItem.order)
        ).all()

        coll_dict = coll.model_dump()
        coll_dict["video_count"] = len(items)
        coll_dict["first_video_id"] = items[0].video_id if items else None

        # 添加 owner 信息
        if coll.owner:
            user_roles = session.exec(select(UserRole).where(UserRole.user_id == coll.owner.id)).all()
            role_ids = [ur.role_id for ur in user_roles]
            role_names = []
            for rid in role_ids:
                role = session.get(Role, rid)
                if role:
                    role_names.append(role.name)
            coll_dict["owner"] = {
                "id": str(coll.owner.id),
                "username": coll.owner.username,
                "email": coll.owner.email,
                "is_active": coll.owner.is_active,
                "is_admin": coll.owner.is_admin,
                "role_ids": role_ids,
                "role_names": role_names,
                "created_at": coll.owner.created_at,
                "avatar_path": coll.owner.avatar_path,
                "bio": coll.owner.bio,
            }

        result.append(coll_dict)

    return result


@router.get("/collections/{collection_id}")
async def get_collection(
    collection_id: UUID,
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: Session = Depends(get_session)
):
    """
    获取合集详情
    """
    collection = session.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    # 获取合集中的视频
    items = session.exec(
        select(CollectionItem)
        .where(CollectionItem.collection_id == collection_id)
        .order_by(CollectionItem.order)
    ).all()

    video_ids = [item.video_id for item in items]
    videos_dict = {}
    if video_ids:
        videos = session.exec(select(Video).where(Video.id.in_(video_ids))).all()
        videos_dict = {v.id: v for v in videos}

    # 按 video_ids 顺序重建数组，保证合集视频顺序固定
    collection_dict = collection.model_dump()
    collection_dict["videos"] = [videos_dict[vid] for vid in video_ids if vid in videos_dict]

    return collection_dict


@router.post("/collections/{collection_id}/videos")
async def add_video_to_collection(
    collection_id: UUID,
    video_id: UUID = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    添加视频到合集
    """
    collection = session.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    if collection.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # 检查是否已在合集中
    existing = session.exec(
        select(CollectionItem).where(
            CollectionItem.collection_id == collection_id,
            CollectionItem.video_id == video_id
        )
    ).first()

    if existing:
        return {"message": "Video already in collection"}

    # 获取当前位置
    last_item = session.exec(
        select(CollectionItem)
        .where(CollectionItem.collection_id == collection_id)
        .order_by(CollectionItem.order.desc())
    ).first()
    new_order = (last_item.order + 1) if last_item else 0

    item = CollectionItem(
        collection_id=collection_id,
        video_id=video_id,
        order=new_order
    )
    session.add(item)
    session.commit()

    return {"message": "Video added to collection"}


@router.delete("/collections/{collection_id}/videos/{video_id}")
async def remove_video_from_collection(
    collection_id: UUID,
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    从合集移除视频
    """
    collection = session.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    if collection.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    item = session.exec(
        select(CollectionItem).where(
            CollectionItem.collection_id == collection_id,
            CollectionItem.video_id == video_id
        )
    ).first()

    if item:
        session.delete(item)
        session.commit()

    return {"message": "Video removed from collection"}


@router.put("/collections/{collection_id}/reorder")
async def reorder_collection(
    collection_id: UUID,
    video_ids: List[UUID] = Body(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    重新排序合集中的视频
    """
    collection = session.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    if collection.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    for position, video_id in enumerate(video_ids):
        item = session.exec(
            select(CollectionItem).where(
                CollectionItem.collection_id == collection_id,
                CollectionItem.video_id == video_id
            )
        ).first()

        if item:
            item.order = position
            session.add(item)

    session.commit()

    return {"message": "Collection reordered"}


@router.delete("/collections/{collection_id}")
async def delete_collection(
    collection_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    删除合集
    """
    collection = session.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    if collection.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 删除合集中的项目
    items = session.exec(
        select(CollectionItem).where(CollectionItem.collection_id == collection_id)
    ).all()

    for item in items:
        session.delete(item)

    session.delete(collection)
    session.commit()

    return {"message": "Collection deleted"}
