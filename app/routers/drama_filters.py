"""
正剧筛选选项管理路由
"""
import json
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from database import get_session
from data_models import DramaFilterOption, FilterTab
from dependencies import get_current_user, PermissionChecker

router = APIRouter(prefix="/admin/filters", tags=["管理后台-正剧筛选选项"])


# ============ FilterTab 模型 ============

class FilterTabRead(BaseModel):
    id: int
    slug: str
    name: str
    filter_type: str = "multi"  # single 或 multi
    display_order: int
    is_active: bool
    option_count: int = 0

    class Config:
        from_attributes = True


class FilterTabCreate(BaseModel):
    slug: str
    name: str
    filter_type: str = "multi"
    display_order: int = 0


class FilterTabUpdate(BaseModel):
    slug: Optional[str] = None
    name: Optional[str] = None
    filter_type: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


# ============ DramaFilterOption 模型 ============

class DramaFilterOptionRead(BaseModel):
    id: int
    tab_slug: str
    value: str
    drama_types: Optional[List[str]] = None
    display_order: int
    is_active: bool
    usage_count: int

    class Config:
        from_attributes = True


class DramaFilterOptionCreate(BaseModel):
    tab_slug: str
    value: str
    drama_types: Optional[List[str]] = None  # ["movie", "tv", "anime"] or None for all
    display_order: int = 0


class DramaFilterOptionUpdate(BaseModel):
    value: Optional[str] = None
    drama_types: Optional[List[str]] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class ReorderItem(BaseModel):
    id: int
    display_order: int


class ReorderRequest(BaseModel):
    orders: List[ReorderItem]


class DramaFilterResponse(BaseModel):
    """公开的筛选选项响应"""
    regions: List[str]
    languages: List[str]
    styles: List[str]
    statuses: List[str]  # 状态筛选（完结/连载）


# 允许的正剧类型
DRAMA_TYPES = ["movie", "tv", "anime"]


def drama_types_to_json(drama_types: Optional[List[str]]) -> Optional[str]:
    if drama_types is None:
        return None
    return json.dumps(drama_types)


def json_to_drama_types(json_str: Optional[str]) -> Optional[List[str]]:
    if json_str is None:
        return None
    try:
        return json.loads(json_str)
    except:
        return None


@router.get("/options", response_model=List[DramaFilterOptionRead])
async def list_filter_options(
    tab_slug: Optional[str] = None,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    获取所有筛选选项（后台管理用）
    """
    statement = select(DramaFilterOption)
    if tab_slug:
        statement = statement.where(DramaFilterOption.tab_slug == tab_slug)
    statement = statement.order_by(DramaFilterOption.display_order)

    options = session.exec(statement).all()

    result = []
    for opt in options:
        result.append(DramaFilterOptionRead(
            id=opt.id,
            tab_slug=opt.tab_slug,
            value=opt.value,
            drama_types=json_to_drama_types(opt.drama_types),
            display_order=opt.display_order,
            is_active=opt.is_active,
            usage_count=opt.usage_count
        ))
    return result


@router.post("/options", response_model=DramaFilterOptionRead)
async def create_filter_option(
    option_data: DramaFilterOptionCreate,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    创建筛选选项
    """
    # 验证 tab_slug 是否存在
    tab = session.exec(
        select(FilterTab).where(FilterTab.slug == option_data.tab_slug)
    ).first()
    if not tab:
        raise HTTPException(status_code=400, detail="无效的 Tab")

    # 检查是否已存在
    existing = session.exec(
        select(DramaFilterOption).where(
            DramaFilterOption.tab_slug == option_data.tab_slug,
            DramaFilterOption.value == option_data.value
        )
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="该选项已存在")

    # 验证 drama_types
    if option_data.drama_types:
        for dt in option_data.drama_types:
            if dt not in DRAMA_TYPES:
                raise HTTPException(status_code=400, detail=f"无效的正剧类型: {dt}")

    option = DramaFilterOption(
        tab_slug=option_data.tab_slug,
        value=option_data.value,
        drama_types=drama_types_to_json(option_data.drama_types),
        display_order=option_data.display_order
    )
    session.add(option)
    session.commit()
    session.refresh(option)

    return DramaFilterOptionRead(
        id=option.id,
        tab_slug=option.tab_slug,
        value=option.value,
        drama_types=json_to_drama_types(option.drama_types),
        display_order=option.display_order,
        is_active=option.is_active,
        usage_count=option.usage_count
    )


@router.put("/options/{option_id}", response_model=DramaFilterOptionRead)
async def update_filter_option(
    option_id: int,
    option_data: DramaFilterOptionUpdate,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    更新筛选选项
    """
    option = session.get(DramaFilterOption, option_id)
    if not option:
        raise HTTPException(status_code=404, detail="选项不存在")

    if option_data.value is not None:
        option.value = option_data.value
    if option_data.drama_types is not None:
        for dt in option_data.drama_types:
            if dt not in DRAMA_TYPES:
                raise HTTPException(status_code=400, detail=f"无效的正剧类型: {dt}")
        option.drama_types = drama_types_to_json(option_data.drama_types)
    if option_data.display_order is not None:
        option.display_order = option_data.display_order
    if option_data.is_active is not None:
        option.is_active = option_data.is_active

    option.updated_at = datetime.utcnow()
    session.add(option)
    session.commit()
    session.refresh(option)

    return DramaFilterOptionRead(
        id=option.id,
        tab_slug=option.tab_slug,
        value=option.value,
        drama_types=json_to_drama_types(option.drama_types),
        display_order=option.display_order,
        is_active=option.is_active,
        usage_count=option.usage_count
    )


@router.delete("/options/{option_id}")
async def delete_filter_option(
    option_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    删除筛选选项
    """
    option = session.get(DramaFilterOption, option_id)
    if not option:
        raise HTTPException(status_code=404, detail="选项不存在")

    session.delete(option)
    session.commit()

    return {"message": "删除成功"}


@router.post("/options/increment")
async def increment_usage(
    tab_slug: str,
    value: str,
    session: Session = Depends(get_session)
):
    """
    增加选项使用次数（公开接口）
    """
    option = session.exec(
        select(DramaFilterOption).where(
            DramaFilterOption.tab_slug == tab_slug,
            DramaFilterOption.value == value
        )
    ).first()

    if option:
        option.usage_count += 1
        option.updated_at = datetime.utcnow()
        session.add(option)
        session.commit()

    return {"message": "ok"}


@router.put("/options/reorder")
async def reorder_filter_options(
    request: ReorderRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    批量更新筛选选项排序
    """
    for item in request.orders:
        option = session.get(DramaFilterOption, item.id)
        if option:
            option.display_order = item.display_order
            option.updated_at = datetime.utcnow()
            session.add(option)

    session.commit()
    return {"success": True}


# ============ FilterTab CRUD ============

@router.get("/tabs", response_model=List[FilterTabRead])
async def list_filter_tabs(
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    获取所有筛选 Tab（含选项数量）
    """
    tabs = session.exec(
        select(FilterTab).order_by(FilterTab.display_order)
    ).all()

    result = []
    for tab in tabs:
        # 计算该 Tab 下的选项数量
        option_count = session.exec(
            select(DramaFilterOption).where(DramaFilterOption.tab_slug == tab.slug)
        ).all()
        result.append(FilterTabRead(
            id=tab.id,
            slug=tab.slug,
            name=tab.name,
            filter_type=tab.filter_type or "multi",
            display_order=tab.display_order,
            is_active=tab.is_active,
            option_count=len(option_count)
        ))
    return result


@router.post("/tabs", response_model=FilterTabRead)
async def create_filter_tab(
    tab_data: FilterTabCreate,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    创建新 Tab
    """
    # 检查 slug 是否已存在
    existing = session.exec(
        select(FilterTab).where(FilterTab.slug == tab_data.slug)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="该 slug 已存在")

    tab = FilterTab(
        slug=tab_data.slug,
        name=tab_data.name,
        filter_type=tab_data.filter_type,
        display_order=tab_data.display_order
    )
    session.add(tab)
    session.commit()
    session.refresh(tab)

    return FilterTabRead(
        id=tab.id,
        slug=tab.slug,
        name=tab.name,
        filter_type=tab.filter_type or "multi",
        display_order=tab.display_order,
        is_active=tab.is_active,
        option_count=0
    )


@router.put("/tabs/reorder")
async def reorder_filter_tabs(
    request: ReorderRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    批量更新 Tab 排序
    """
    for item in request.orders:
        tab = session.get(FilterTab, item.id)
        if tab:
            tab.display_order = item.display_order
            session.add(tab)

    session.commit()
    return {"success": True}


@router.put("/tabs/{tab_id}", response_model=FilterTabRead)
async def update_filter_tab(
    tab_id: int,
    tab_data: FilterTabUpdate,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    更新 Tab
    """
    tab = session.get(FilterTab, tab_id)
    if not tab:
        raise HTTPException(status_code=404, detail="Tab 不存在")

    # 检查 slug 是否重复（排除自己）
    if tab_data.slug is not None and tab_data.slug != tab.slug:
        existing = session.exec(select(FilterTab).where(FilterTab.slug == tab_data.slug)).first()
        if existing:
            raise HTTPException(status_code=400, detail="该 slug 已存在")
        tab.slug = tab_data.slug
    if tab_data.name is not None:
        tab.name = tab_data.name
    if tab_data.filter_type is not None:
        tab.filter_type = tab_data.filter_type
    if tab_data.display_order is not None:
        tab.display_order = tab_data.display_order
    if tab_data.is_active is not None:
        tab.is_active = tab_data.is_active

    session.add(tab)
    session.commit()
    session.refresh(tab)

    # 获取选项数量
    option_count = session.exec(
        select(DramaFilterOption).where(DramaFilterOption.tab_slug == tab.slug)
    ).all()

    return FilterTabRead(
        id=tab.id,
        slug=tab.slug,
        name=tab.name,
        filter_type=tab.filter_type or "multi",
        display_order=tab.display_order,
        is_active=tab.is_active,
        option_count=len(option_count)
    )


@router.delete("/tabs/{tab_id}")
async def delete_filter_tab(
    tab_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    删除 Tab（同时删除关联的选项）
    """
    tab = session.get(FilterTab, tab_id)
    if not tab:
        raise HTTPException(status_code=404, detail="Tab 不存在")

    # 删除关联的选项
    options = session.exec(
        select(DramaFilterOption).where(DramaFilterOption.tab_slug == tab.slug)
    ).all()
    for opt in options:
        session.delete(opt)

    session.delete(tab)
    session.commit()

    return {"message": "删除成功"}
