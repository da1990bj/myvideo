"""
推荐系统路由
"""
from typing import List, Optional
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlmodel import Session, select

from database import get_session
from data_models import (
    User, Video, VideoRead, UserRead,
    VideoLike, VideoFavorite,
    VideoRecommendation, VideoRecommendationRead, VideoRecommendationWithVideoRead,
    VideoRecommendationCreate, VideoRecommendationUpdate,
    RecommendationSlot, RecommendationSlotRead, RecommendationSlotCreate, RecommendationSlotUpdate,
    RecommendationLog, UserVideoScore,
    RecommendationResponse, RecommendationsListResponse
)
from dependencies import get_current_user, get_current_user_optional
from cache_manager import get_cache, RecommendationCache
from recommendation_engine import RecommendationEngine
from tasks import compute_all_recommendation_scores

router = APIRouter(prefix="", tags=["推荐系统"])


@router.get("/recommendations", response_model=RecommendationsListResponse)
async def get_recommendations(
    slot_name: str,
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
    exclude_video_ids: Optional[List[str]] = Query(None),
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: Session = Depends(get_session)
):
    """
    获取推荐视频列表 (支持缓存)
    """
    try:
        # 转换exclude_video_ids为UUID
        exclude_ids = []
        if exclude_video_ids:
            for vid_str in exclude_video_ids:
                try:
                    exclude_ids.append(UUID(vid_str))
                except ValueError:
                    pass

        # 缓存逻辑
        cache = get_cache()

        # 尝试从缓存获取推荐ID列表
        cached_recs = cache.get(
            slot_name,
            user_id=None,
            limit=limit
        )

        if cached_recs:
            recommendations = cached_recs
        else:
            # 计算推荐
            engine = RecommendationEngine(session, current_user.id if current_user else None)
            recommendations = await engine.get_recommendations_for_slot(
                slot_name=slot_name,
                limit=limit,
                exclude_video_ids=exclude_ids
            )
            # 保存到缓存
            cache.set(slot_name, recommendations, ttl=RecommendationCache.CACHE_CONFIG.get(slot_name))

        # 处理offset
        recommendations = recommendations[offset:]

        # 转换为响应格式
        result = []
        for rec in recommendations:
            video = session.get(Video, rec["video_id"])
            if video:
                # 检查用户是否点赞和收藏
                is_liked = False
                is_favorited = False
                if current_user:
                    is_liked = session.exec(
                        select(VideoLike).where(
                            VideoLike.user_id == current_user.id,
                            VideoLike.video_id == video.id
                        )
                    ).first() is not None

                    is_favorited = session.exec(
                        select(VideoFavorite).where(
                            VideoFavorite.user_id == current_user.id,
                            VideoFavorite.video_id == video.id
                        )
                    ).first() is not None

                video_read = VideoRead(
                    id=video.id,
                    title=video.title,
                    description=video.description,
                    status=video.status,
                    visibility=video.visibility,
                    processed_file_path=video.processed_file_path,
                    thumbnail_path=video.thumbnail_path,
                    duration=video.duration,
                    views=video.views,
                    complete_views=video.complete_views,
                    like_count=video.like_count,
                    favorite_count=video.favorite_count,
                    is_liked=is_liked,
                    is_favorited=is_favorited,
                    created_at=video.created_at,
                    tags=video.tags,
                    owner=UserRead(
                        id=video.owner.id,
                        username=video.owner.username,
                        email=video.owner.email,
                        is_active=video.owner.is_active,
                        is_admin=video.owner.is_admin,
                        role_id=video.owner.role_id,
                        created_at=video.owner.created_at,
                        avatar_path=video.owner.avatar_path,
                        bio=video.owner.bio
                    ) if video.owner else None,
                    category=video.category
                )

                result.append(RecommendationResponse(
                    video=video_read,
                    score=rec["score"],
                    source=rec["source"],
                    reason=rec["reason"]
                ))

        # 获取推荐位信息
        slot = session.exec(
            select(RecommendationSlot).where(RecommendationSlot.slot_name == slot_name)
        ).first()

        slot_info = {
            "slot_name": slot_name,
            "display_title": slot.display_title if slot else slot_name
        }

        return RecommendationsListResponse(
            recommendations=result,
            slot_info=slot_info
        )

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error getting recommendations: {e}")
        raise HTTPException(status_code=500, detail="Failed to get recommendations")


@router.post("/recommendations/click")
async def track_recommendation_click(
    request_body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """追踪用户对推荐的点击"""
    try:
        video_id = request_body.get("video_id")
        slot_name = request_body.get("slot_name")
        impression_rank = request_body.get("impression_rank", 0)

        video_uuid = UUID(video_id)

        # 更新推荐日志
        log = session.exec(
            select(RecommendationLog).where(
                RecommendationLog.user_id == current_user.id,
                RecommendationLog.video_id == video_uuid,
                RecommendationLog.slot_name == slot_name,
                RecommendationLog.impression_rank == impression_rank
            )
        ).first()

        if log:
            log.clicked = True
            log.clicked_at = datetime.utcnow()
            session.add(log)
        else:
            new_log = RecommendationLog(
                user_id=current_user.id,
                video_id=video_uuid,
                recommendation_source="unknown",
                slot_name=slot_name,
                impression_rank=impression_rank,
                clicked=True,
                clicked_at=datetime.utcnow()
            )
            session.add(new_log)

        session.commit()
        return {"status": "ok"}

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error tracking click: {e}")
        raise HTTPException(status_code=500, detail="Failed to track click")


@router.post("/recommendations/watch")
async def track_recommendation_watch(
    request_body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """追踪用户观看推荐的完成度"""
    try:
        video_id = request_body.get("video_id")
        watch_percent = request_body.get("watch_percent", 0)

        video_uuid = UUID(video_id)

        # 更新推荐日志
        log = session.exec(
            select(RecommendationLog).where(
                RecommendationLog.user_id == current_user.id,
                RecommendationLog.video_id == video_uuid
            )
        ).first()

        if log:
            log.watched_percent = max(log.watched_percent or 0, watch_percent)
            session.add(log)

        session.commit()
        return {"status": "ok"}

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error tracking watch: {e}")
        raise HTTPException(status_code=500, detail="Failed to track watch")


# ==================== 管理端推荐系统 API ====================

@router.get("/admin/recommendations", response_model=List[VideoRecommendationWithVideoRead])
async def get_manual_recommendations(
    admin: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    slot_name: Optional[str] = None
):
    """获取手动推荐的视频"""
    statement = select(VideoRecommendation)
    if slot_name:
        statement = statement.where(VideoRecommendation.slot_name == slot_name)

    recommendations = session.exec(statement.order_by(VideoRecommendation.slot_position)).all()

    result = []
    for rec in recommendations:
        video = session.get(Video, rec.video_id)
        if video:
            result.append(VideoRecommendationWithVideoRead(
                **rec.model_dump(),
                video=video
            ))

    return result


@router.post("/admin/recommendations", response_model=VideoRecommendationRead, status_code=201)
async def create_manual_recommendation(
    recommendation: VideoRecommendationCreate,
    admin: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """添加手动推荐"""
    rec_data = recommendation.model_dump()
    rec_data["created_by"] = admin.id
    rec = VideoRecommendation(**rec_data)
    session.add(rec)
    session.commit()
    session.refresh(rec)

    # 清除缓存
    cache = get_cache()
    try:
        cache.clear_slot_cache(recommendation.slot_name)
    except Exception:
        pass

    return rec


@router.put("/admin/recommendations/{rec_id}", response_model=VideoRecommendationRead)
async def update_manual_recommendation(
    rec_id: str,
    recommendation: VideoRecommendationUpdate,
    admin: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """更新手动推荐"""
    rec = session.get(VideoRecommendation, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    update_data = recommendation.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(rec, key, value)

    session.add(rec)
    session.commit()
    session.refresh(rec)

    # 清除缓存
    cache = get_cache()
    try:
        cache.clear_slot_cache(rec.slot_name)
    except Exception:
        pass

    return rec


@router.delete("/admin/recommendations/{rec_id}", status_code=204)
async def delete_manual_recommendation(
    rec_id: str,
    admin: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """删除手动推荐"""
    rec = session.get(VideoRecommendation, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    slot_name = rec.slot_name
    session.delete(rec)
    session.commit()

    # 清除缓存
    cache = get_cache()
    try:
        cache.clear_slot_cache(slot_name)
    except Exception:
        pass

    return None


@router.get("/admin/recommendation-slots", response_model=List[RecommendationSlotRead])
async def get_recommendation_slots(
    admin: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """获取推荐位列表"""
    return session.exec(select(RecommendationSlot)).all()


@router.post("/admin/recommendation-slots", response_model=RecommendationSlotRead, status_code=201)
async def create_recommendation_slot(
    slot: RecommendationSlotCreate,
    admin: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """创建推荐位"""
    existing = session.exec(
        select(RecommendationSlot).where(RecommendationSlot.slot_name == slot.slot_name)
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="Slot already exists")

    new_slot = RecommendationSlot(**slot.model_dump())
    session.add(new_slot)
    session.commit()
    session.refresh(new_slot)

    return new_slot


@router.put("/admin/recommendation-slots/{slot_id}", response_model=RecommendationSlotRead)
async def update_recommendation_slot(
    slot_id: str,
    slot: RecommendationSlotUpdate,
    admin: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """更新推荐位"""
    db_slot = session.get(RecommendationSlot, slot_id)
    if not db_slot:
        raise HTTPException(status_code=404, detail="Slot not found")

    update_data = slot.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_slot, key, value)

    session.add(db_slot)
    session.commit()
    session.refresh(db_slot)

    return db_slot


@router.get("/admin/recommendations/analytics")
async def get_recommendation_analytics(
    admin: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    slot_name: Optional[str] = None
):
    """获取推荐效果分析数据"""
    statement = select(RecommendationLog)
    if slot_name:
        statement = statement.where(RecommendationLog.slot_name == slot_name)

    logs = session.exec(statement).all()

    total_impressions = len(logs)
    total_clicks = len([l for l in logs if l.clicked])
    total_watched = sum(l.watched_percent or 0 for l in logs)

    ctr = (total_clicks / total_impressions * 100) if total_impressions > 0 else 0

    return {
        "user_engagement": {
            "total_impressions": total_impressions,
            "total_clicks": total_clicks,
            "click_through_rate": round(ctr, 2),
            "total_watched": total_watched,
        },
        "top_performing": [],
        "by_source": {},
    }


@router.post("/admin/recommendations/recompute")
async def recompute_recommendations(
    admin: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """手动触发推荐分数重新计算"""
    task = compute_all_recommendation_scores.delay()
    return {"message": "Recommendation recomputation started", "task_id": task.id}
