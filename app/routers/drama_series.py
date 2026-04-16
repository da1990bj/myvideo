"""
剧集系列管理路由（电视剧/动漫/电影）
"""
from typing import List, Optional
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from pydantic import BaseModel
from sqlmodel import Session, select

from database import get_session
from data_models import DramaSeries, DramaSeriesItem, Video, User
from dependencies import get_current_user, PermissionChecker

router = APIRouter(prefix="/drama-series", tags=["剧集系列"])
admin_router = APIRouter(prefix="/admin/drama-series", tags=["管理后台-剧集系列"])


# ============ Schema ============

class DramaSeriesCreate(BaseModel):
    title: str
    description: Optional[str] = None
    cover_image: Optional[str] = None
    drama_type: str  # "movie", "tv", "anime"
    drama_kind: Optional[str] = None  # 类型子分类: 番剧, 剧场版, 电影
    drama_region: Optional[str] = None    # 地区 - 单选
    drama_language: Optional[str] = None  # 语言 - 单选
    drama_style: Optional[List[str]] = None
    drama_year: Optional[int] = None
    drama_status: Optional[str] = None
    total_episodes: Optional[int] = None
    rating: Optional[float] = None        # 评分 0-10
    is_public: bool = True


class DramaSeriesUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    cover_image: Optional[str] = None
    drama_type: Optional[str] = None    # 类型: movie, tv, anime
    drama_kind: Optional[str] = None    # 类型子分类: 番剧, 剧场版, 电影
    drama_region: Optional[str] = None    # 地区 - 单选
    drama_language: Optional[str] = None  # 语言 - 单选
    drama_style: Optional[List[str]] = None
    drama_year: Optional[int] = None
    drama_status: Optional[str] = None
    total_episodes: Optional[int] = None
    rating: Optional[float] = None        # 评分 0-10
    is_public: Optional[bool] = None


class DramaSeriesRead(BaseModel):
    id: UUID
    title: str
    description: Optional[str] = None
    cover_image: Optional[str] = None
    drama_type: str
    drama_kind: Optional[str] = None    # 类型子分类: 番剧, 剧场版, 电影
    drama_region: Optional[str] = None    # 地区 - 单选
    drama_language: Optional[str] = None  # 语言 - 单选
    drama_style: Optional[List[str]] = None
    drama_year: Optional[int] = None
    drama_status: Optional[str] = None
    total_episodes: Optional[int] = None
    rating: Optional[float] = None        # 评分 0-10
    is_public: bool = True
    view_count: int = 0
    created_at: datetime
    video_count: int = 0
    owner: Optional[dict] = None
    videos: Optional[List[dict]] = None     # 视频列表（管理集数用）


class ReorderItem(BaseModel):
    id: int
    order: int


class ReorderRequest(BaseModel):
    orders: List[ReorderItem]


# ============ 公开 API ============

@router.get("", response_model=List[DramaSeriesRead])
async def list_drama_series(
    drama_type: Optional[str] = Query(None, description="剧集类型: movie, tv, anime"),
    session: Session = Depends(get_session)
):
    """
    获取剧集系列列表（公开）
    """
    query = select(DramaSeries).where(DramaSeries.is_public == True)
    if drama_type:
        query = query.where(DramaSeries.drama_type == drama_type)
    query = query.order_by(DramaSeries.created_at.desc())

    series_list = session.exec(query).all()

    result = []
    for s in series_list:
        # 计算视频数量
        items = session.exec(
            select(DramaSeriesItem).where(DramaSeriesItem.series_id == s.id)
        ).all()

        item_count = len(items)

        series_dict = s.model_dump()
        series_dict["video_count"] = item_count

        # 获取 owner 信息
        if s.user_id:
            user = session.get(User, s.user_id)
            if user:
                series_dict["owner"] = {
                    "id": str(user.id),
                    "username": user.username
                }

        result.append(series_dict)

    return result


@router.get("/{series_id}", response_model=DramaSeriesRead)
async def get_drama_series(
    series_id: UUID,
    session: Session = Depends(get_session)
):
    """
    获取剧集系列详情（含视频列表）
    """
    series = session.get(DramaSeries, series_id)
    if not series:
        raise HTTPException(status_code=404, detail="剧集系列不存在")

    # 获取视频列表
    items = session.exec(
        select(DramaSeriesItem)
        .where(DramaSeriesItem.series_id == series_id)
        .order_by(DramaSeriesItem.order)
    ).all()

    series_dict = series.model_dump()
    series_dict["video_count"] = len(items)
    series_dict["videos"] = []

    # 获取每个视频的详细信息
    for item in items:
        video = session.get(Video, item.video_id)
        if video:
            series_dict["videos"].append({
                "id": str(video.id),
                "title": video.title,
                "thumbnail_path": video.thumbnail_path,
                "duration": video.duration,
                "views": video.views,
                "episode_number": item.episode_number,
                "processed_file_path": video.processed_file_path,
                "original_file_path": video.original_file_path,
                "status": video.status,
                "is_approved": video.is_approved,
                "visibility": video.visibility
            })

    # 获取 owner 信息
    if series.user_id:
        user = session.get(User, series.user_id)
        if user:
            series_dict["owner"] = {
                "id": str(user.id),
                "username": user.username
            }

    return series_dict


# ============ 管理后台 API ============

@admin_router.post("", response_model=DramaSeriesRead)
async def create_drama_series(
    data: DramaSeriesCreate,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    创建剧集系列（管理员）
    """
    series = DramaSeries(
        title=data.title,
        description=data.description,
        cover_image=data.cover_image,
        drama_type=data.drama_type,
        drama_kind=data.drama_kind,
        drama_region=data.drama_region,
        drama_language=data.drama_language,
        drama_style=data.drama_style,
        drama_year=data.drama_year,
        drama_status=data.drama_status,
        total_episodes=data.total_episodes,
        rating=data.rating,
        is_public=data.is_public,
        user_id=current_user.id
    )
    session.add(series)
    session.commit()
    session.refresh(series)

    return series


@admin_router.put("/{series_id}", response_model=DramaSeriesRead)
async def update_drama_series(
    series_id: UUID,
    data: DramaSeriesUpdate,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    更新剧集系列（管理员）
    """
    series = session.get(DramaSeries, series_id)
    if not series:
        raise HTTPException(status_code=404, detail="剧集系列不存在")

    if data.title is not None:
        series.title = data.title
    if data.description is not None:
        series.description = data.description
    if data.cover_image is not None:
        series.cover_image = data.cover_image
    if data.drama_type is not None:
        series.drama_type = data.drama_type
    if data.drama_kind is not None:
        series.drama_kind = data.drama_kind
    if data.drama_region is not None:
        series.drama_region = data.drama_region
    if data.drama_language is not None:
        series.drama_language = data.drama_language
    if data.drama_style is not None:
        series.drama_style = data.drama_style
    if data.drama_year is not None:
        series.drama_year = data.drama_year
    if data.drama_status is not None:
        series.drama_status = data.drama_status
    if data.total_episodes is not None:
        series.total_episodes = data.total_episodes
    if data.rating is not None:
        series.rating = data.rating
    if data.is_public is not None:
        series.is_public = data.is_public

    series.updated_at = datetime.utcnow()
    session.add(series)
    session.commit()
    session.refresh(series)

    return series


@admin_router.delete("/{series_id}")
async def delete_drama_series(
    series_id: UUID,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    删除剧集系列（管理员）
    """
    series = session.get(DramaSeries, series_id)
    if not series:
        raise HTTPException(status_code=404, detail="剧集系列不存在")

    # 删除关联的 items（级联删除，但显式删除更清晰）
    items = session.exec(
        select(DramaSeriesItem).where(DramaSeriesItem.series_id == series_id)
    ).all()
    for item in items:
        session.delete(item)

    session.delete(series)
    session.commit()

    return {"message": "删除成功"}


@admin_router.post("/{series_id}/videos")
async def add_video_to_series(
    series_id: UUID,
    video_id: UUID = Body(..., embed=True),
    episode_number: Optional[int] = Body(None),
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    添加视频到剧集系列（管理员）
    """
    series = session.get(DramaSeries, series_id)
    if not series:
        raise HTTPException(status_code=404, detail="剧集系列不存在")

    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")

    # 检查是否已存在
    existing = session.exec(
        select(DramaSeriesItem).where(
            DramaSeriesItem.series_id == series_id,
            DramaSeriesItem.video_id == video_id
        )
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="视频已在剧集中")

    # 获取当前最大 order
    max_order = session.exec(
        select(DramaSeriesItem.order).where(DramaSeriesItem.series_id == series_id)
        .order_by(DramaSeriesItem.order.desc())
    ).first()
    next_order = (max_order or 0) + 1

    item = DramaSeriesItem(
        series_id=series_id,
        video_id=video_id,
        order=next_order,
        episode_number=episode_number
    )
    session.add(item)

    # 更新视频的 series_id
    video.series_id = series_id

    session.commit()
    return {"message": "添加成功"}


@admin_router.delete("/{series_id}/videos/{video_id}")
async def remove_video_from_series(
    series_id: UUID,
    video_id: UUID,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    从剧集系列移除视频（管理员）
    """
    item = session.exec(
        select(DramaSeriesItem).where(
            DramaSeriesItem.series_id == series_id,
            DramaSeriesItem.video_id == video_id
        )
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="视频不在剧集中")

    # 清除视频的 series_id
    video = session.get(Video, video_id)
    if video:
        video.series_id = None

    session.delete(item)
    session.commit()

    return {"message": "移除成功"}


@admin_router.put("/{series_id}/reorder")
async def reorder_series_videos(
    series_id: UUID,
    request: ReorderRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(PermissionChecker("admin:super"))
):
    """
    调整剧集系列中的视频顺序（管理员）
    """
    series = session.get(DramaSeries, series_id)
    if not series:
        raise HTTPException(status_code=404, detail="剧集系列不存在")

    for item in request.orders:
        item_record = session.get(DramaSeriesItem, item.id)
        if item_record and item_record.series_id == series_id:
            item_record.order = item.order
            session.add(item_record)

    session.commit()
    return {"success": True}