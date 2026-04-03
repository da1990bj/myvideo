"""
推荐系统缓存模块
使用Redis实现推荐结果缓存，减少数据库查询
"""

import json
import logging
import redis
from uuid import UUID
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from config import settings


class UUIDEncoder(json.JSONEncoder):
    """支持UUID和datetime的JSON编码器"""
    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

logger = logging.getLogger(__name__)

class RecommendationCache:
    """推荐结果缓存管理器"""

    def __init__(self, redis_url: str = None):
        """初始化缓存连接"""
        # Use settings.REDIS_URL as default
        redis_url = redis_url or settings.REDIS_URL
        try:
            self.redis_client = redis.from_url(redis_url, decode_responses=True)
            # 测试连接
            self.redis_client.ping()
            self.enabled = True
            logger.info("✅ Redis缓存已启用")
        except Exception as e:
            logger.warning(f"⚠️ Redis连接失败，将使用本地缓存: {e}")
            self.enabled = False
            self.local_cache = {}

    # ==================== 缓存配置 ====================
    CACHE_CONFIG = {
        "home_carousel": 3600,              # 1小时
        "category_featured": 7200,          # 2小时
        "sidebar_related": 1800,            # 30分钟
        "trending": 1800,                   # 30分钟
        "personalized": 900,                # 15分钟（用户个性化推荐变化快）
    }

    # ==================== 缓存键生成 ====================
    @staticmethod
    def _make_key(slot_name: str, user_id: Optional[str] = None, **kwargs) -> str:
        """生成缓存键"""
        exclude_params = {"limit", "offset"}
        params = {k: str(v) for k, v in kwargs.items() if k not in exclude_params}

        if user_id:
            key = f"rec:user:{user_id}:slot:{slot_name}"
        else:
            # 为非授权用户生成缓存键（基于推荐位）
            key = f"rec:public:slot:{slot_name}"

        # 加入过滤参数（如 category_id）以区分不同查询
        if params:
            filter_str = ":".join(f"{k}={v}" for k, v in sorted(params.items()))
            key = f"{key}:{filter_str}"

        return key

    @staticmethod
    def _make_user_key(user_id: str) -> str:
        """生成用户推荐缓存键前缀"""
        return f"rec:user:{user_id}:*"

    # ==================== 缓存操作 ====================
    def get(self, slot_name: str, user_id: Optional[str] = None, **kwargs) -> Optional[List[Dict[str, Any]]]:
        """获取缓存的推荐"""
        cache_key = self._make_key(slot_name, user_id, **kwargs)

        if self.enabled:
            try:
                cached = self.redis_client.get(cache_key)
                if cached:
                    logger.debug(f"📦 缓存命中: {cache_key}")
                    return json.loads(cached)
            except Exception as e:
                logger.warning(f"⚠️ 缓存读取失败: {e}")
        else:
            # 使用本地缓存
            if cache_key in self.local_cache:
                cached_data, expiry = self.local_cache[cache_key]
                if datetime.now() < expiry:
                    logger.debug(f"📦 本地缓存命中: {cache_key}")
                    return cached_data
                else:
                    del self.local_cache[cache_key]

        return None

    def set(
        self,
        slot_name: str,
        recommendations: List[Dict[str, Any]],
        user_id: Optional[str] = None,
        ttl: Optional[int] = None,
        **kwargs
    ) -> bool:
        """设置缓存的推荐"""
        cache_key = self._make_key(slot_name, user_id, **kwargs)
        ttl = ttl or self.CACHE_CONFIG.get(slot_name, 3600)

        try:
            cache_value = json.dumps(recommendations, cls=UUIDEncoder)

            if self.enabled:
                self.redis_client.setex(cache_key, ttl, cache_value)
                logger.debug(f"💾 缓存已设置: {cache_key} (TTL: {ttl}s)")
            else:
                # 本地缓存
                expiry = datetime.now() + timedelta(seconds=ttl)
                self.local_cache[cache_key] = (recommendations, expiry)
                logger.debug(f"💾 本地缓存已设置: {cache_key}")

            return True
        except Exception as e:
            logger.error(f"❌ 缓存设置失败: {e}")
            return False

    def delete(self, slot_name: str, user_id: Optional[str] = None, **kwargs) -> bool:
        """删除缓存"""
        cache_key = self._make_key(slot_name, user_id, **kwargs)

        try:
            if self.enabled:
                self.redis_client.delete(cache_key)
            else:
                if cache_key in self.local_cache:
                    del self.local_cache[cache_key]

            logger.debug(f"🗑️ 缓存已删除: {cache_key}")
            return True
        except Exception as e:
            logger.error(f"❌ 缓存删除失败: {e}")
            return False

    def clear_user_cache(self, user_id: str) -> int:
        """清空用户所有缓存"""
        try:
            if self.enabled:
                # 获取所有用户相关的缓存键
                pattern = self._make_user_key(user_id)
                keys = self.redis_client.keys(pattern)
                if keys:
                    self.redis_client.delete(*keys)
                    logger.debug(f"🗑️ 已清空用户 {user_id} 的 {len(keys)} 个缓存")
                    return len(keys)
            else:
                # 本地缓存清理
                user_prefix = f"rec:user:{user_id}:"
                keys_to_delete = [k for k in self.local_cache if k.startswith(user_prefix)]
                for key in keys_to_delete:
                    del self.local_cache[key]
                logger.debug(f"🗑️ 已清空用户 {user_id} 的本地缓存")
                return len(keys_to_delete)
            return 0
        except Exception as e:
            logger.error(f"❌ 用户缓存清空失败: {e}")
            return 0

    def clear_slot_cache(self, slot_name: str) -> int:
        """清空推荐位所有缓存"""
        try:
            if self.enabled:
                # 清空该推荐位的所有缓存（公开+用户）
                public_pattern = f"rec:public:slot:{slot_name}*"
                user_pattern = f"rec:user:*:slot:{slot_name}*"

                public_keys = self.redis_client.keys(public_pattern)
                user_keys = self.redis_client.keys(user_pattern)

                all_keys = public_keys + user_keys
                if all_keys:
                    self.redis_client.delete(*all_keys)
                    logger.debug(f"🗑️ 已清空推荐位 {slot_name} 的 {len(all_keys)} 个缓存")
                    return len(all_keys)
            else:
                # 本地缓存清理
                prefix = f":slot:{slot_name}"
                keys_to_delete = [k for k in self.local_cache if prefix in k]
                for key in keys_to_delete:
                    del self.local_cache[key]
                logger.info(f"🗑️ 已清空推荐位 {slot_name} 的本地缓存")
                return len(keys_to_delete)
            return 0
        except Exception as e:
            logger.error(f"❌ 推荐位缓存清空失败: {e}")
            return 0

    # ==================== 缓存统计 ====================
    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        try:
            if self.enabled:
                info = self.redis_client.info()
                return {
                    "backend": "redis",
                    "used_memory": info.get("used_memory_human", "N/A"),
                    "connected_clients": info.get("connected_clients", 0),
                    "total_commands": info.get("total_commands_processed", 0),
                    "status": "✅ 运行中"
                }
            else:
                return {
                    "backend": "local",
                    "cache_entries": len(self.local_cache),
                    "status": "⚠️ 本地缓存模式"
                }
        except Exception as e:
            logger.error(f"❌ 缓存统计失败: {e}")
            return {"status": "❌ 错误", "error": str(e)}

    def warm_up(self, recommendations_data: Dict[str, List[Dict[str, Any]]]) -> int:
        """缓存预热 - 应用启动时调用"""
        """
        预热推荐缓存

        用法:
            data = {
                "home_carousel": [...],
                "trending": [...]
            }
            cache.warm_up(data)
        """
        count = 0
        for slot_name, recommendations in recommendations_data.items():
            if self.set(slot_name, recommendations):
                count += 1
                logger.info(f"✅ 已预热: {slot_name}")

        total_items = sum(len(recs) for recs in recommendations_data.values())
        logger.info(f"✅ 缓存预热完成: {count} 个推荐位, {total_items} 条推荐")
        return count


# 全局缓存实例
_cache_instance = None

def get_cache() -> RecommendationCache:
    """获取缓存实例（单例）"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = RecommendationCache(settings.REDIS_URL)
    return _cache_instance


# 缓存装饰器
def cache_recommendation(slot_name: str, ttl: Optional[int] = None):
    """缓存推荐结果的装饰器"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # 获取用户ID（如果有）
            user_id = kwargs.get("user_id")

            cache = get_cache()
            cached = cache.get(slot_name, user_id)

            if cached:
                return cached

            # 如果没有缓存，调用原函数
            result = await func(*args, **kwargs)

            # 保存到缓存
            cache.set(slot_name, result, user_id, ttl)

            return result

        return wrapper
    return decorator
