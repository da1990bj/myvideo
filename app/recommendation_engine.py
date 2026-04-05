"""
推荐算法引擎
支持4种推荐策略的混合推荐系统
"""
import logging
from typing import List, Optional, Tuple, Union
from uuid import UUID
from datetime import datetime, timedelta
from sqlmodel import Session, select, func
from sqlmodel.sql.expression import Select

from data_models import (
    Video, User, UserVideoHistory, VideoLike, VideoFavorite,
    Tag, VideoTag, Category, UserVideoScore, VideoRecommendation,
    RecommendationSlot, RecommendationLog, SystemConfig
)

logger = logging.getLogger(__name__)


class RecommendationEngine:
    """
    推荐算法引擎

    支持4种推荐策略：
    1. 协同过滤 (40%) - 基于相似用户的观看行为
    2. 热门内容 (30%) - 最近7天的热门视频
    3. 分类相似 (20%) - 用户偏好分类
    4. 标签相似 (10%) - 点赞/收藏视频的标签
    """

    def __init__(self, session: Session, user_id: Optional[UUID] = None):
        self.session = session
        self.user_id = user_id
        self.weights = self._load_weights()

    def _load_weights(self) -> dict:
        """从system_config加载权重配置"""
        weights = {
            "collaborative": 0.40,
            "trending": 0.30,
            "category": 0.20,
            "tag": 0.10
        }

        try:
            # 尝试从数据库读取配置的权重
            for key in weights.keys():
                config_key = f"recommendation_weight_{key}"
                stmt = select(SystemConfig).where(SystemConfig.key == config_key)
                config = self.session.exec(stmt).first()
                if config:
                    weights[key] = float(config.value)
        except Exception as e:
            logger.warning(f"Failed to load weights from config: {e}, using defaults")

        return weights

    async def get_collaborative_recommendations(self, limit: int = 10) -> List[Tuple[UUID, float]]:
        """
        基于观看历史的协同过滤推荐

        逻辑：
        1. 找与当前用户观看相似的其他用户
        2. 获取这些用户看过但当前用户没看的视频
        3. 按热度排序返回

        返回: [(video_id, score), ...]
        """
        if not self.user_id:
            return []

        try:
            # 获取当前用户观看过的视频
            user_watched = set(
                self.session.exec(
                    select(UserVideoHistory.video_id)
                    .where(UserVideoHistory.user_id == self.user_id)
                ).all()
            )

            if not user_watched:
                return []

            # 找看过类似视频的其他用户（至少看过一个相同视频）
            similar_users = self.session.exec(
                select(UserVideoHistory.user_id)
                .where(UserVideoHistory.video_id.in_(user_watched))
                .where(UserVideoHistory.user_id != self.user_id)
                .distinct()
                .limit(100)  # 限制相似用户数量
            ).all()

            if not similar_users:
                return []

            # 获取这些用户看过但当前用户没看的视频，按热度排序
            stmt = (
                select(Video.id, func.sum(Video.views + Video.like_count * 2 + Video.favorite_count * 3).label("score"))
                .select_from(UserVideoHistory)
                .join(Video, UserVideoHistory.video_id == Video.id)
                .where(UserVideoHistory.user_id.in_(similar_users))
                .where(~UserVideoHistory.video_id.in_(user_watched))
                .where(Video.status == "published")
                .where(Video.visibility == "public")
                .group_by(Video.id)
                .order_by(func.sum(Video.views + Video.like_count * 2 + Video.favorite_count * 3).desc())
                .limit(limit * 2)
            )

            results = self.session.exec(stmt).all()
            return [(video_id, float(score or 0) / 100.0) for video_id, score in results[:limit]]

        except Exception as e:
            logger.error(f"Error in collaborative recommendations: {e}")
            return []

    async def get_trending_recommendations(self, days: int = 7, limit: int = 10, category_id: Optional[int] = None) -> List[Tuple[UUID, float]]:
        """
        获取最近N天的热门视频

        热度公式: sqrt(views) + likes*2 + favorites*3

        Args:
            days: 统计天数
            limit: 返回数量
            category_id: 可选，按分类过滤
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            # 先查询视频的基本数据
            statement = (
                select(Video)
                .where(Video.created_at >= cutoff_date)
                .where(Video.status.in_(["completed", "approved"]))
                .where(Video.visibility == "public")
            )
            if category_id:
                statement = statement.where(Video.category_id == category_id)

            videos = self.session.exec(statement).all()

            # 如果视频数量少于请求的limit，扩展到30天以获取更多热门视频
            if len(videos) < limit:
                cutoff_date = datetime.utcnow() - timedelta(days=30)
                statement = (
                    select(Video)
                    .where(Video.created_at >= cutoff_date)
                    .where(Video.status.in_(["completed", "approved"]))
                    .where(Video.visibility == "public")
                )
                if category_id:
                    statement = statement.where(Video.category_id == category_id)
                videos = self.session.exec(statement).all()

            # 计算热度分数
            scored_videos = []
            for v in videos:
                score = (v.views ** 0.5) + v.like_count * 2 + v.favorite_count * 3
                scored_videos.append((v.id, float(score)))

            # 排序并限制数量
            scored_videos.sort(key=lambda x: x[1], reverse=True)

            # 归一化分数到 0-100
            if scored_videos:
                max_score = scored_videos[0][1] if scored_videos[0][1] > 0 else 1
                return [(vid, min(float(score / max_score * 100), 100.0)) for vid, score in scored_videos[:limit]]

            return []

        except Exception as e:
            logger.error(f"Error in trending recommendations: {e}")
            return []

    async def get_category_recommendations(self, limit: int = 10) -> List[Tuple[UUID, float]]:
        """
        基于用户观看历史的分类偏好推荐

        逻辑：
        1. 统计用户观看过的各分类视频数
        2. 计算分类权重
        3. 推荐top分类中的新/热门视频
        """
        if not self.user_id:
            return []

        try:
            # 获取用户观看过的分类分布
            watched_videos = self.session.exec(
                select(UserVideoHistory.video_id)
                .where(UserVideoHistory.user_id == self.user_id)
            ).all()

            if not watched_videos:
                return []

            category_stats = self.session.exec(
                select(Video.category_id, func.count(Video.id).label("count"))
                .where(Video.id.in_(watched_videos))
                .group_by(Video.category_id)
                .order_by(func.count(Video.id).desc())
            ).all()

            if not category_stats:
                return []

            # 获取top分类
            top_categories = [cat_id for cat_id, _ in category_stats[:5]]

            # 在这些分类中推荐未看过的视频
            stmt = (
                select(Video.id, (Video.views + Video.like_count * 2 + Video.favorite_count * 3).label("score"))
                .where(Video.category_id.in_(top_categories))
                .where(~Video.id.in_(watched_videos))
                .where(Video.status == "published")
                .where(Video.visibility == "public")
                .order_by((Video.views + Video.like_count * 2 + Video.favorite_count * 3).desc())
                .limit(limit)
            )

            results = self.session.exec(stmt).all()

            if results:
                max_score = max(score for _, score in results) or 1
                return [(video_id, min(float(score / max_score * 100), 100.0)) for video_id, score in results]

            return []

        except Exception as e:
            logger.error(f"Error in category recommendations: {e}")
            return []

    async def get_tag_recommendations(self, limit: int = 10) -> List[Tuple[UUID, float]]:
        """
        基于点赞/收藏视频标签的推荐

        逻辑：
        1. 获取用户点赞/收藏的视频
        2. 提取这些视频的所有标签
        3. 推荐包含这些标签的其他视频
        """
        if not self.user_id:
            return []

        try:
            # 获取用户点赞的视频
            liked_videos = set(
                self.session.exec(
                    select(VideoLike.video_id)
                    .where(VideoLike.user_id == self.user_id)
                    .where(VideoLike.like_type == "like")
                ).all()
            )

            # 获取用户收藏的视频
            favorited_videos = set(
                self.session.exec(
                    select(VideoFavorite.video_id)
                    .where(VideoFavorite.user_id == self.user_id)
                ).all()
            )

            user_engaged_videos = liked_videos | favorited_videos

            if not user_engaged_videos:
                return []

            # 获取这些视频的所有标签
            user_tags = self.session.exec(
                select(VideoTag.tag_id, func.count(VideoTag.tag_id).label("count"))
                .where(VideoTag.video_id.in_(user_engaged_videos))
                .group_by(VideoTag.tag_id)
                .order_by(func.count(VideoTag.tag_id).desc())
            ).all()

            if not user_tags:
                return []

            top_tag_ids = [tag_id for tag_id, _ in user_tags[:10]]

            # 获取包含这些标签的其他视频
            stmt = (
                select(Video.id, (Video.views + Video.like_count * 2 + Video.favorite_count * 3).label("score"))
                .select_from(VideoTag)
                .join(Video, VideoTag.video_id == Video.id)
                .where(VideoTag.tag_id.in_(top_tag_ids))
                .where(~Video.id.in_(user_engaged_videos))
                .where(Video.status == "published")
                .where(Video.visibility == "public")
                .distinct(Video.id)
                .order_by((Video.views + Video.like_count * 2 + Video.favorite_count * 3).desc())
                .limit(limit)
            )

            results = self.session.exec(stmt).all()

            if results:
                max_score = max(score for _, score in results) or 1
                return [(video_id, min(float(score / max_score * 100), 100.0)) for video_id, score in results]

            return []

        except Exception as e:
            logger.error(f"Error in tag recommendations: {e}")
            return []

    async def compute_all_scores(self) -> Tuple[List[Tuple[UUID, float]], dict]:
        """
        计算用户的所有推荐分数

        返回最终的推荐视频列表，按综合分排序
        """
        if not self.user_id:
            return [], {}

        try:
            # 并行执行4种推荐策略
            collaborative = await self.get_collaborative_recommendations(limit=100)
            trending = await self.get_trending_recommendations(limit=100)
            category = await self.get_category_recommendations(limit=100)
            tag = await self.get_tag_recommendations(limit=100)

            # 合并所有推荐
            score_map: dict[UUID, dict] = {}

            for video_id, score in collaborative:
                score_map.setdefault(video_id, {})["collaborative"] = score * self.weights["collaborative"]

            for video_id, score in trending:
                score_map.setdefault(video_id, {})["trending"] = score * self.weights["trending"]

            for video_id, score in category:
                score_map.setdefault(video_id, {})["category"] = score * self.weights["category"]

            for video_id, score in tag:
                score_map.setdefault(video_id, {})["tag"] = score * self.weights["tag"]

            # 计算最终分数
            final_scores = []
            for video_id, scores in score_map.items():
                final_score = sum(scores.values())
                final_scores.append((video_id, final_score, scores))

            # 按最终分排序
            final_scores.sort(key=lambda x: x[1], reverse=True)

            return [(vid, score) for vid, score, _ in final_scores], {vid: scores for vid, _, scores in final_scores}

        except Exception as e:
            logger.error(f"Error computing scores: {e}")
            return [], {}

    async def get_recommendations_for_slot(
        self,
        slot_name: str,
        limit: int = 10,
        exclude_video_ids: Optional[List[UUID]] = None,
        category_id: Optional[int] = None
    ) -> List[dict]:
        """
        根据推荐位配置返回最终推荐

        流程：
        1. 获取推荐位配置
        2. 根据策略加载手动推荐和算法推荐
        3. 合并、去重、排序
        4. 记录到recommendation_logs

        Args:
            slot_name: 推荐位名称
            limit: 返回数量
            exclude_video_ids: 排除的视频ID列表
            category_id: 可选，按分类过滤（主要用于热门推荐）

        返回: [{"video_id": UUID, "score": float, "source": str, "reason": str}, ...]
        """
        exclude_video_ids = exclude_video_ids or []

        try:
            # 1. 获取推荐位配置
            stmt = select(RecommendationSlot).where(RecommendationSlot.slot_name == slot_name)
            slot = self.session.exec(stmt).first()

            if not slot or not slot.enabled:
                logger.warning(f"Recommendation slot {slot_name} not found or disabled")
                return []

            # 2. 检查登录状态
            if not self.user_id:
                # 未登录用户
                if not slot.show_unauthenticated:
                    return []
                # 注意：即使 unauthenticated_strategy == "trending_only"，仍然继续加载手动推荐
                # trending_only 只影响算法推荐的加载，不影响手动推荐

            # 3. 根据策略加载推荐
            recommendations = {}

            # 3a. 加载手动推荐（但当指定 category_id 时，对于 trending 跳过手动推荐以避免显示错误分类）
            manual_recs = []
            skip_manual_for_category = (slot_name == "trending" and category_id)
            if slot.recommendation_strategy in ["manual_first", "mixed"] and not skip_manual_for_category:
                # 兼容旧数据：slot_name 可能对应旧的 recommendation_type
                # home_carousel <-> featured_carousel
                type_mapping = {
                    "home_carousel": ["home_carousel", "featured_carousel"],
                    "category_featured": ["category_featured", "category_featured"],
                }
                valid_types = type_mapping.get(slot.slot_name, [slot.slot_name])

                manual_recs = self.session.exec(
                    select(VideoRecommendation)
                    .where(VideoRecommendation.recommendation_type.in_(valid_types))
                    .where(VideoRecommendation.enabled == True)
                    .where(
                        (VideoRecommendation.expires_at == None) |
                        (VideoRecommendation.expires_at > datetime.utcnow())
                    )
                    .order_by(VideoRecommendation.priority.desc(), VideoRecommendation.slot_position)
                    .limit(slot.max_items // 2 if slot.recommendation_strategy == "mixed" else slot.max_items)
                ).all()

                for rec in manual_recs:
                    if rec.video_id not in exclude_video_ids:
                        recommendations[rec.video_id] = {
                            "score": 100.0,  # 手动推荐最高分
                            "source": "manual",
                            "reason": rec.reason or "编辑精选推荐"
                        }

            # 3b. 手动推荐不足时 fallback 到热门推荐
            # manual_first: 少于 max_items 时补充
            # mixed: 少于 max_items // 2 时补充
            expected_manual = slot.max_items if slot.recommendation_strategy == "manual_first" else slot.max_items // 2
            needs_fallback = len(recommendations) < expected_manual

            # 3c. 加载算法推荐
            if slot.recommendation_strategy in ["algorithm_only", "mixed"] or needs_fallback:
                # 对于 trending 推荐位且指定了 category_id，使用热门算法过滤
                if slot_name == "trending" and category_id:
                    trending_recs = await self.get_trending_recommendations(
                        days=7,
                        limit=slot.max_items,
                        category_id=category_id
                    )
                    for video_id, score in trending_recs:
                        if video_id not in exclude_video_ids and video_id not in recommendations:
                            recommendations[video_id] = {
                                "score": score,
                                "source": "trending",
                                "reason": "热门推荐"
                            }
                # manual_first fallback 到热门推荐，或者有 category_id 时也需要过滤
                elif needs_fallback or category_id:
                    trending_recs = await self.get_trending_recommendations(
                        days=7,
                        limit=slot.max_items - len(recommendations),
                        category_id=category_id
                    )
                    for video_id, score in trending_recs:
                        if video_id not in exclude_video_ids and video_id not in recommendations:
                            recommendations[video_id] = {
                                "score": score,
                                "source": "trending",
                                "reason": "热门推荐"
                            }
                elif self.user_id:
                    # 尝试从缓存加载
                    cached_scores = self.session.exec(
                        select(UserVideoScore)
                        .where(UserVideoScore.user_id == self.user_id)
                        .order_by(UserVideoScore.final_score.desc())
                        .limit(slot.max_items * 2)
                    ).all()

                    algo_limit = slot.max_items - len(recommendations) if slot.recommendation_strategy == "mixed" else slot.max_items

                    for score_record in cached_scores:
                        if score_record.video_id not in exclude_video_ids and score_record.video_id not in recommendations:
                            # 根据缓存记录确定推荐来源
                            source = "collaborative" if score_record.collaborative_score > 0 else "trending"
                            recommendations[score_record.video_id] = {
                                "score": score_record.final_score,
                                "source": source,
                                "reason": self._get_reason(score_record)
                            }

                            if len(recommendations) >= slot.max_items:
                                break

            # 4. 获取视频对象并准备返回
            result = []
            for rank, (video_id, info) in enumerate(list(recommendations.items())[:limit], 1):
                video = self.session.exec(
                    select(Video).where(Video.id == video_id)
                ).first()

                if video and video.status in ("completed", "approved") and video.visibility == "public":
                    result.append({
                        "video_id": video_id,
                        "score": info["score"],
                        "source": info["source"],
                        "reason": info["reason"],
                        "rank": rank
                    })

                    # 5. 记录到日志（异步，不阻塞）
                    if self.user_id:
                        try:
                            log = RecommendationLog(
                                user_id=self.user_id,
                                video_id=video_id,
                                recommendation_source=info["source"],
                                slot_name=slot_name,
                                impression_rank=rank,
                                clicked=False,
                                watched=False
                            )
                            self.session.add(log)
                        except Exception as e:
                            logger.error(f"Error logging recommendation: {e}")

            try:
                self.session.commit()
            except Exception as e:
                logger.error(f"Error committing recommendation logs: {e}")
                self.session.rollback()

            return result

        except Exception as e:
            logger.error(f"Error getting recommendations for slot {slot_name}: {e}")
            return []

    def _get_reason(self, score_record: UserVideoScore) -> str:
        """根据分数记录生成推荐理由"""
        if score_record.collaborative_score > score_record.trending_score:
            return "基于你的观看历史"
        elif score_record.trending_score > 0:
            return "热门推荐"
        elif score_record.category_score > 0:
            return "你关注的分类"
        else:
            return "根据你的兴趣"


async def compute_user_recommendation_scores(session: Session, user_id: UUID) -> int:
    """
    计算单个用户的所有推荐分数
    返回成功处理的视频数
    """
    engine = RecommendationEngine(session, user_id)

    try:
        final_scores, score_details = await engine.compute_all_scores()

        # 清除旧的分数记录
        session.exec(
            select(UserVideoScore)
            .where(UserVideoScore.user_id == user_id)
        )
        for old_score in session.exec(
            select(UserVideoScore)
            .where(UserVideoScore.user_id == user_id)
        ).all():
            session.delete(old_score)

        # 保存新的分数（只保存top 100）
        for video_id, final_score in final_scores[:100]:
            scores = score_details.get(video_id, {})
            score_record = UserVideoScore(
                user_id=user_id,
                video_id=video_id,
                collaborative_score=scores.get("collaborative", 0),
                similarity_score=0,  # 保留用于未来扩展
                category_score=scores.get("category", 0),
                tag_score=scores.get("tag", 0),
                final_score=final_score,
                last_updated=datetime.utcnow()
            )
            session.add(score_record)

        session.commit()
        logger.info(f"Computed recommendations for user {user_id}: {len(final_scores)} videos processed")
        return len(final_scores)

    except Exception as e:
        logger.error(f"Error computing recommendations for user {user_id}: {e}")
        session.rollback()
        return 0
