"""
分类管理路由
"""
from typing import List, Optional
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from sqlalchemy import asc

from database import get_session
from data_models import Category, Video
from dependencies import get_current_user, PermissionChecker

router = APIRouter(prefix="/admin/categories", tags=["管理后台-分类"])


class CategoryCreate(BaseModel):
    name: str
    slug: str
    display_order: int = 0


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    display_order: Optional[int] = None


class CategoryResponse(BaseModel):
    id: int
    name: str
    slug: str
    display_order: int = 0
    video_count: int = 0

    class Config:
        from_attributes = True


def category_to_response(category: Category, video_count: int = 0) -> CategoryResponse:
    return CategoryResponse(
        id=category.id,
        name=category.name,
        slug=category.slug,
        display_order=category.display_order,
        video_count=video_count
    )


@router.get("", response_model=List[CategoryResponse])
async def get_categories(
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("video:audit"))
):
    """
    获取所有分类（含视频数量）
    """
    categories = session.exec(select(Category).order_by(asc(Category.display_order))).all()

    result = []
    for cat in categories:
        count = session.exec(select(Video).where(Video.category_id == cat.id, Video.is_deleted == False)).all()
        result.append(category_to_response(cat, len(count)))

    return result


@router.post("", response_model=CategoryResponse)
async def create_category(
    category_data: CategoryCreate,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    创建新分类
    """
    # 检查名称是否重复
    existing = session.exec(select(Category).where(Category.name == category_data.name)).first()
    if existing:
        raise HTTPException(status_code=400, detail="分类名称已存在")

    # 检查 slug 是否重复
    existing_slug = session.exec(select(Category).where(Category.slug == category_data.slug)).first()
    if existing_slug:
        raise HTTPException(status_code=400, detail="分类slug已存在")

    category = Category(name=category_data.name, slug=category_data.slug, display_order=category_data.display_order)
    session.add(category)
    session.commit()
    session.refresh(category)

    return category_to_response(category, 0)


@router.put("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: int,
    category_data: CategoryUpdate,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    修改分类
    """
    category = session.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="分类不存在")

    # 检查名称是否重复（排除自己）
    if category_data.name and category_data.name != category.name:
        existing = session.exec(select(Category).where(Category.name == category_data.name)).first()
        if existing:
            raise HTTPException(status_code=400, detail="分类名称已存在")

    # 检查 slug 是否重复（排除自己）
    if category_data.slug and category_data.slug != category.slug:
        existing_slug = session.exec(select(Category).where(Category.slug == category_data.slug)).first()
        if existing_slug:
            raise HTTPException(status_code=400, detail="分类slug已存在")

    if category_data.name is not None:
        category.name = category_data.name
    if category_data.slug is not None:
        category.slug = category_data.slug
    if category_data.display_order is not None:
        category.display_order = category_data.display_order

    session.add(category)
    session.commit()
    session.refresh(category)

    # 获取视频数量
    count = len(session.exec(select(Video).where(Video.category_id == category.id, Video.is_deleted == False)).all())

    return category_to_response(category, count)


@router.post("/reorder")
async def reorder_categories(
    orders: List[dict],  # [{"id": 1, "display_order": 0}, {"id": 2, "display_order": 1}, ...]
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    批量更新分类排序
    """
    for item in orders:
        category = session.get(Category, item["id"])
        if category:
            category.display_order = item["display_order"]
            session.add(category)

    session.commit()
    return {"message": "排序已更新"}


@router.delete("/{category_id}")
async def delete_category(
    category_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    删除分类（如果有视频关联则拒绝删除）
    """
    category = session.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="分类不存在")

    # 检查是否有视频关联
    videos = session.exec(select(Video).where(Video.category_id == category_id, Video.is_deleted == False)).all()
    if videos:
        raise HTTPException(
            status_code=400,
            detail=f"该分类下有 {len(videos)} 个视频，无法删除。请先将这些视频移到其他分类。"
        )

    session.delete(category)
    session.commit()

    return {"message": "分类删除成功"}
