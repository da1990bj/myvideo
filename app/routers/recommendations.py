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
    User, Video, VideoRead, UserRead, Role, UserRole,
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


def _build_user_read(owner: User, session: Session) -> UserRead:
    """构建UserRead，兼容多角色"""
    user_roles = session.exec(select(UserRole).where(UserRole.user_id == owner.id)).all()
    role_ids = [ur.role_id for ur in user_roles]
    role_names = []
    for rid in role_ids:
        role = session.get(Role, rid)
        if role:
            role_names.append(role.name)
    return UserRead(
        id=owner.id,
        username=owner.username,
        email=owner.email,
        is_active=owner.is_active,
        is_admin=owner.is_admin,
        role_ids=role_ids,
        role_names=role_names,
        created_at=owner.created_at,
        avatar_path=owner.avatar_path,
        bio=owner.bio
    )


@router.get("/recommendations", response_model=RecommendationsListResponse)
async def get_recommendations(
    slot_name: str,
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
    exclude_video_ids: Optional[List[str]] = Query(None),
    category_id: Optional[str] = Query(None),
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

        # 转换category_id为整数
        cat_id = None
        if category_id:
            try:
                cat_id = int(category_id)
            except ValueError:
                pass

        # 缓存逻辑
        cache = get_cache()

        # 尝试从 Redis Sorted Set 获取
        recommendations = []
        user_id_str = str(current_user.id) if current_user else None

        if slot_name == "personalized" and user_id_str:
            # 个性化推荐从 Redis Sorted Set 读取
            redis_recs = cache.zrange_user_recommendations(user_id_str, start=0, end=limit+99, withscores=True)
            if redis_recs:
                recommendations = [{"video_id": UUID(vid), "score": score, "source": "collaborative", "reason": "个性化推荐"} for vid, score in redis_recs]
            else:
                # Redis 没有缓存，计算并存储
                engine = RecommendationEngine(session, current_user.id)
                recommendations = await engine.get_recommendations_for_slot(
                    slot_name=slot_name,
                    limit=limit,
                    exclude_video_ids=exclude_ids,
                    category_id=cat_id
                )
                if recommendations:
                    cache.zadd_user_recommendations(user_id_str, [(r["video_id"], r["score"]) for r in recommendations])

        elif slot_name == "trending" and cat_id:
            # 分类热门从 Redis 读取
            redis_recs = cache.zrange_trending_category(cat_id, start=0, end=limit+99, withscores=True)
            if redis_recs:
                recommendations = [{"video_id": UUID(vid), "score": score, "source": "trending", "reason": "热门推荐"} for vid, score in redis_recs]

        elif slot_name == "trending":
            # 全局热门从 Redis 读取
            redis_recs = cache.zrange_trending(start=0, end=limit+99, withscores=True)
            if redis_recs:
                recommendations = [{"video_id": UUID(vid), "score": score, "source": "trending", "reason": "热门推荐"} for vid, score in redis_recs]

        # 如果 Redis 没有数据，尝试旧的缓存
        if not recommendations:
            cached_recs = cache.get(
                slot_name,
                user_id=None,
                limit=limit,
                category_id=cat_id
            )
            if cached_recs:
                recommendations = cached_recs

        # 如果缓存也没有，尝试使用推荐引擎计算
        if not recommendations:
            engine = RecommendationEngine(session, current_user.id if current_user else None)
            recommendations = await engine.get_recommendations_for_slot(
                slot_name=slot_name,
                limit=limit,
                exclude_video_ids=exclude_ids,
                category_id=cat_id
            )

        # 处理offset
        recommendations = recommendations[offset:]

        # 转换为响应格式
        result = []
        for rec in recommendations:
            video = session.get(Video, rec["video_id"])
            if video and video.visibility == "public":
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
                    is_approved=video.is_approved,
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
                    owner=_build_user_read(video.owner, session) if video.owner else None,
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
            # 来源映射：根据 slot_name 判断
            rec_source = "unknown"
            if slot_name == "home_carousel":
                rec_source = "trending"
            elif slot_name == "personalized":
                rec_source = "collaborative"
            elif slot_name == "sidebar_related":
                rec_source = "similarity"
            elif slot_name == "category_featured":
                rec_source = "category"
            else:
                rec_source = "manual"

            new_log = RecommendationLog(
                user_id=current_user.id,
                video_id=video_uuid,
                recommendation_source=rec_source,
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
        slot_name = request_body.get("slot_name")
        impression_rank = request_body.get("impression_rank")

        video_uuid = UUID(video_id)

        # 更新推荐日志，优先按 slot_name 和 impression_rank 查找
        query = select(RecommendationLog).where(
            RecommendationLog.user_id == current_user.id,
            RecommendationLog.video_id == video_uuid
        )
        if slot_name is not None and impression_rank is not None:
            query = query.where(
                RecommendationLog.slot_name == slot_name,
                RecommendationLog.impression_rank == impression_rank
            )
        log = session.exec(query).first()

        if log:
            log.watched = True
            log.watched_duration = max(log.watched_duration or 0, watch_percent)
            session.add(log)
        elif slot_name is not None:
            # 如果没找到日志但有slot信息，创建新记录
            # 来源映射：根据 slot_name 判断
            rec_source = "unknown"
            if slot_name == "home_carousel":
                rec_source = "trending"
            elif slot_name == "personalized":
                rec_source = "collaborative"
            elif slot_name == "sidebar_related":
                rec_source = "similarity"
            elif slot_name == "category_featured":
                rec_source = "category"
            else:
                rec_source = "manual"

            new_log = RecommendationLog(
                user_id=current_user.id,
                video_id=video_uuid,
                recommendation_source=rec_source,
                slot_name=slot_name,
                impression_rank=impression_rank or 0,
                watched=True,
                watched_duration=watch_percent
            )
            session.add(new_log)

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
    total_watched = len([l for l in logs if l.watched])

    ctr = (total_clicks / total_impressions * 100) if total_impressions > 0 else 0

    # 获取所有推荐位名称映射
    slots = session.exec(select(RecommendationSlot)).all()
    slot_name_map = {s.slot_name: s.display_title or s.slot_name for s in slots}

    # 按视频统计 top_performing
    video_ids = list(set(str(log.video_id) for log in logs))
    videos = {str(v.id): v for v in session.exec(select(Video).where(Video.id.in_(video_ids), Video.is_deleted == False)).all()}

    video_stats = {}
    for log in logs:
        vid = str(log.video_id)
        if vid not in video_stats:
            video = videos.get(vid)
            video_stats[vid] = {
                "video_id": vid,
                "video_title": video.title if video else "未知",
                "category": video.category.name if video and video.category else "未知",
                "author": video.owner.username if video and video.owner else "未知",
                "impressions": 0, "clicks": 0, "watched": 0
            }
        video_stats[vid]["impressions"] += 1
        if log.clicked:
            video_stats[vid]["clicks"] += 1
        if log.watched:
            video_stats[vid]["watched"] += 1

    # 计算每个视频的 CTR 并排序
    for vid, stats in video_stats.items():
        stats["ctr"] = round((stats["clicks"] / stats["impressions"] * 100), 2) if stats["impressions"] > 0 else 0
    top_performing = sorted(video_stats.values(), key=lambda x: x["clicks"], reverse=True)[:10]

    # 按来源统计 by_source（按推荐位 slot_name 分组，显示推荐位标题）
    source_stats = {}
    for log in logs:
        slot = log.slot_name or "unknown"
        display_name = slot_name_map.get(slot, slot)
        if slot not in source_stats:
            source_stats[slot] = {"source": display_name, "slot_name": slot, "impressions": 0, "clicks": 0, "watched": 0}
        source_stats[slot]["impressions"] += 1
        if log.clicked:
            source_stats[slot]["clicks"] += 1
        if log.watched:
            source_stats[slot]["watched"] += 1
    for slot, stats in source_stats.items():
        stats["ctr"] = round((stats["clicks"] / stats["impressions"] * 100), 2) if stats["impressions"] > 0 else 0

    return {
        "user_engagement": {
            "total_impressions": total_impressions,
            "total_clicks": total_clicks,
            "click_through_rate": round(ctr, 2),
            "total_watched": total_watched,
        },
        "top_performing": top_performing,
        "by_source": source_stats,
    }


@router.get("/admin/scheduled-tasks")
async def get_scheduled_tasks(
    admin: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """获取所有定时任务列表"""
    from tasks import celery_app

    beat_schedule = celery_app.conf.beat_schedule

    tasks = []
    for task_name, task_config in beat_schedule.items():
        schedule = task_config.get("schedule", {})
        # 解析 crontab 格式
        if hasattr(schedule, 'minute') and hasattr(schedule, 'hour'):
            minute = schedule.minute if isinstance(schedule.minute, str) else str(schedule.minute)
            hour = schedule.hour if isinstance(schedule.hour, str) else str(schedule.hour)
            if minute.startswith('*/'):
                schedule_str = f"每{hour.replace('*', '0')}分钟执行一次" if hour == '*' else f"每{minute[2:]}分钟"
            elif minute == '0' and hour != '*':
                schedule_str = f"每天 {hour}:00"
            else:
                schedule_str = f"每天 {hour}:{minute.zfill(2)}"
        elif hasattr(schedule, '__float__'):
            # timedelta 格式
            seconds = int(float(schedule.total_seconds()))
            if seconds >= 3600:
                schedule_str = f"每{seconds // 3600}小时"
            elif seconds >= 60:
                schedule_str = f"每{seconds // 60}分钟"
            else:
                schedule_str = f"每{seconds}秒"
        else:
            schedule_str = str(schedule)

        # 获取任务描述
        task_descriptions = {
            "compute-daily-trending": "计算每日热门视频，存储到数据库",
            "compute-category-trending": "按分类计算热门视频",
            "cold-storage-migration-daily": "冷存储迁移任务",
            "transcode-aging-hourly": "更新转码老化状态",
            "zombie-ffmpeg-cleanup": "清理僵尸 FFmpeg 进程",
        }

        tasks.append({
            "task_name": task_name,
            "task_path": task_config.get("task", ""),
            "schedule": schedule_str,
            "description": task_descriptions.get(task_name, ""),
            "queue": task_config.get("options", {}).get("queue", "default"),
        })

    return {"tasks": tasks}


@router.post("/admin/recommendations/recompute")
async def recompute_recommendations(
    admin: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """手动触发推荐分数重新计算"""
    task = compute_all_recommendation_scores.delay()
    return {"message": "Recommendation recomputation started", "task_id": task.id}
