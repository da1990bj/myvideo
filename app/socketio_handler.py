"""
WebSocket 处理器 - 使用 Redis Pub/Sub 实现分布式实时转码进度推送

该模块独立于 main.py，通过 Redis Pub/Sub 与 Celery workers 解耦。
所有 WebSocket 推送由 FastAPI 主进程处理，Celery 任务只负责发布消息到 Redis。
"""

from typing import Dict, Optional
import logging
from datetime import datetime
import asyncio
import threading

logger = logging.getLogger(__name__)

# Redis 客户端 (延迟初始化，由 init_redis 调用)
_redis_client = None
_pubsub = None
_listener_thread = None


def init_redis(redis_url: str):
    """
    初始化 Redis 连接，用于 Pub/Sub

    Args:
        redis_url: Redis 连接 URL
    """
    global _redis_client, _pubsub, _listener_thread
    import redis
    _redis_client = redis.from_url(redis_url, decode_responses=True)
    _pubsub = _redis_client.pubsub()


def get_redis_client():
    """获取 Redis 客户端实例"""
    return _redis_client


def ensure_redis_client(redis_url: str):
    """确保 Redis 客户端已初始化"""
    global _redis_client
    if _redis_client is None:
        import redis
        _redis_client = redis.from_url(redis_url, decode_responses=True)
    return _redis_client


def publish_notification_count(user_id: str, count: int, redis_url: str = None):
    """
    发布通知计数更新到 Redis 频道

    Args:
        user_id: 用户ID
        count: 未读通知数量
        redis_url: Redis URL (如果已初始化则传None)
    """
    import json
    client = ensure_redis_client(redis_url) if redis_url else _redis_client
    if client:
        try:
            payload = json.dumps({"user_id": str(user_id), "count": count})
            client.publish("notifications:count", payload)
        except Exception as e:
            logger.warning(f"Failed to publish notification count: {e}")


def publish_upload_progress(user_id: str, session_id: str, progress: float,
                            uploaded_chunks: int, total_chunks: int, redis_url: str = None):
    """
    发布上传进度更新到 Redis 频道

    Args:
        user_id: 用户ID
        session_id: 上传会话ID
        progress: 进度百分比 (0-100)
        uploaded_chunks: 已上传分片数
        total_chunks: 总分片数
        redis_url: Redis URL
    """
    import json
    client = ensure_redis_client(redis_url) if redis_url else _redis_client
    if client:
        try:
            payload = json.dumps({
                "user_id": str(user_id),
                "session_id": session_id,
                "progress": progress,
                "uploaded_chunks": uploaded_chunks,
                "total_chunks": total_chunks,
                "type": "upload_progress"
            })
            client.publish("upload:progress", payload)
        except Exception as e:
            logger.warning(f"Failed to publish upload progress: {e}")


def publish_upload_complete(user_id: str, session_id: str, video_id: str, redis_url: str = None):
    """
    发布上传完成事件到 Redis 频道

    Args:
        user_id: 用户ID
        session_id: 上传会话ID
        video_id: 视频ID
        redis_url: Redis URL
    """
    import json
    client = ensure_redis_client(redis_url) if redis_url else _redis_client
    if client:
        try:
            payload = json.dumps({
                "user_id": str(user_id),
                "session_id": session_id,
                "video_id": video_id,
                "type": "upload_complete"
            })
            client.publish("upload:progress", payload)
        except Exception as e:
            logger.warning(f"Failed to publish upload complete: {e}")


class ConnectionManager:
    """
    用户连接池管理器 (保留内存模式，与 socketio 实例配合使用)
    - 维护在线用户与 WebSocket 会话的映射
    - 处理连接/断开事件
    - 注意: 连接状态仍在内存中，通过 socketio Redis Adapter 实现多节点同步
    """

    def __init__(self):
        """初始化连接池"""
        self.active_connections: Dict[str, str] = {}  # user_id (字符串) -> sid (socket id)
        self.user_videos: Dict[str, set] = {}  # user_id -> {processing video_ids}

    async def connect(self, user_id: str, sid: str):
        """
        用户连接时调用

        Args:
            user_id: 用户ID字符串
            sid: WebSocket会话ID
        """
        self.active_connections[user_id] = sid
        if user_id not in self.user_videos:
            self.user_videos[user_id] = set()

        logger.info(f"User {user_id} connected via WebSocket (SID: {sid})")
        return {"status": "connected", "message": f"Welcome user {user_id}"}

    async def disconnect(self, user_id: str):
        """
        用户断开连接时调用

        Args:
            user_id: 用户ID字符串
        """
        if user_id in self.active_connections:
            old_sid = self.active_connections.pop(user_id)
            logger.info(f"User {user_id} disconnected (was SID: {old_sid})")

        if user_id in self.user_videos:
            del self.user_videos[user_id]

    async def disconnect_by_sid(self, sid: str) -> Optional[str]:
        """
        根据SID查找并断开用户连接

        Args:
            sid: WebSocket会话ID

        Returns:
            断开连接的用户ID，如果未找到返回None
        """
        for user_id, user_sid in list(self.active_connections.items()):
            if user_sid == sid:
                await self.disconnect(user_id)
                return user_id
        return None

    async def push_progress(self, sio, user_id: str, video_id: str, progress: int, status: str = "processing"):
        """
        推送转码进度给创作者
        实时将转码进度更新推送到客户端

        Args:
            sio: Socket.IO AsyncServer实例
            user_id: 目标用户ID
            video_id: 视频ID
            progress: 进度百分比 (0-100)
            status: 视频状态 (processing, completed, failed)
        """
        if user_id not in self.active_connections:
            logger.debug(f"User {user_id} not connected, skipping progress push for video {video_id}")
            return

        try:
            sid = self.active_connections[user_id]

            # 跟踪转码中的视频
            if status == "processing":
                self.user_videos[user_id].add(video_id)
            elif status in ("completed", "failed"):
                self.user_videos[user_id].discard(video_id)

            # 推送事件到客户端
            await sio.emit(
                'transcode_progress',
                {
                    'video_id': str(video_id),
                    'progress': progress,
                    'status': status,
                    'timestamp': datetime.utcnow().isoformat()
                },
                room=sid
            )

            logger.debug(f"Pushed progress for video {video_id}: {progress}% to user {user_id}")

        except Exception as e:
            logger.error(f"Error pushing progress to user {user_id}: {e}")

    async def push_subtitle_progress(self, sio, user_id: str, video_id: str, progress: int, status: str = "processing", language: str = ""):
        """
        推送字幕生成进度给用户

        Args:
            sio: Socket.IO AsyncServer实例
            user_id: 目标用户ID
            video_id: 视频ID
            progress: 进度百分比 (0-100)
            status: 状态 (processing, completed, failed)
            language: 字幕语言代码
        """
        if user_id not in self.active_connections:
            logger.debug(f"User {user_id} not connected, skipping subtitle progress push for video {video_id}")
            return

        try:
            sid = self.active_connections[user_id]

            await sio.emit(
                'subtitle_progress',
                {
                    'video_id': str(video_id),
                    'progress': progress,
                    'status': status,
                    'language': language,
                    'timestamp': datetime.utcnow().isoformat()
                },
                room=sid
            )

            logger.debug(f"Pushed subtitle progress for video {video_id}: {progress}% to user {user_id}")

        except Exception as e:
            logger.error(f"Error pushing subtitle progress to user {user_id}: {e}")

    async def push_notification_count(self, sio, user_id: str, count: int):
        """
        推送通知未读数给用户

        Args:
            sio: Socket.IO AsyncServer实例
            user_id: 目标用户ID
            count: 未读通知数量
        """
        if user_id not in self.active_connections:
            logger.debug(f"User {user_id} not connected, skipping notification count push")
            return

        try:
            sid = self.active_connections[user_id]
            await sio.emit(
                'notification_count',
                {'count': count, 'timestamp': datetime.utcnow().isoformat()},
                room=sid
            )
            logger.debug(f"Pushed notification count {count} to user {user_id}")
        except Exception as e:
            logger.error(f"Error pushing notification count to user {user_id}: {e}")

    async def push_upload_progress(self, sio, user_id: str, session_id: str, progress: float,
                                  uploaded_chunks: int, total_chunks: int):
        """
        推送上传进度给用户

        Args:
            sio: Socket.IO AsyncServer实例
            user_id: 目标用户ID
            session_id: 上传会话ID
            progress: 进度百分比
            uploaded_chunks: 已上传分片数
            total_chunks: 总分片数
        """
        if user_id not in self.active_connections:
            logger.debug(f"User {user_id} not connected, skipping upload progress push")
            return

        try:
            sid = self.active_connections[user_id]
            await sio.emit(
                'upload_progress',
                {
                    'session_id': session_id,
                    'progress': progress,
                    'uploaded_chunks': uploaded_chunks,
                    'total_chunks': total_chunks,
                    'timestamp': datetime.utcnow().isoformat()
                },
                room=sid
            )
            logger.debug(f"Pushed upload progress {progress}% for session {session_id} to user {user_id}")
        except Exception as e:
            logger.error(f"Error pushing upload progress to user {user_id}: {e}")

    async def push_upload_complete(self, sio, user_id: str, session_id: str, video_id: str):
        """
        推送上传完成事件给用户

        Args:
            sio: Socket.IO AsyncServer实例
            user_id: 目标用户ID
            session_id: 上传会话ID
            video_id: 视频ID
        """
        if user_id not in self.active_connections:
            logger.debug(f"User {user_id} not connected, skipping upload complete push")
            return

        try:
            sid = self.active_connections[user_id]
            await sio.emit(
                'upload_complete',
                {
                    'session_id': session_id,
                    'video_id': video_id,
                    'timestamp': datetime.utcnow().isoformat()
                },
                room=sid
            )
            logger.debug(f"Pushed upload complete for session {session_id} to user {user_id}")
        except Exception as e:
            logger.error(f"Error pushing upload complete to user {user_id}: {e}")

    async def broadcast_transcode_update(self, sio, event: str, data: dict):
        """
        广播转码队列更新给所有管理员

        Args:
            sio: Socket.IO AsyncServer实例
            event: 事件名称
            data: 事件数据
        """
        try:
            # 广播到 admin 房间的所有连接
            await sio.emit(event, data, room="admin")
            logger.debug(f"Broadcasted {event} to all admins")
        except Exception as e:
            logger.error(f"Error broadcasting to admins: {e}")

    async def push_batch_progress(self, sio, user_id: str, videos_data: list):
        """
        批量推送多个视频的进度（用于连接恢复）

        Args:
            sio: Socket.IO AsyncServer实例
            user_id: 目标用户ID
            videos_data: 视频进度数据列表
        """
        if user_id not in self.active_connections:
            return

        try:
            sid = self.active_connections[user_id]
            await sio.emit(
                'transcode_progress_batch',
                {'videos': videos_data},
                room=sid
            )
            logger.debug(f"Pushed batch progress for {len(videos_data)} videos to user {user_id}")
        except Exception as e:
            logger.error(f"Error pushing batch progress to user {user_id}: {e}")

    def get_connected_users_count(self) -> int:
        """获取当前在线用户数"""
        return len(self.active_connections)

    def get_user_processing_videos(self, user_id: str) -> set:
        """获取某用户正在转码的视频集合"""
        return self.user_videos.get(user_id, set())

    def get_connection_info(self) -> dict:
        """获取连接池统计信息"""
        return {
            "connected_users": self.get_connected_users_count(),
            "total_processing_videos": sum(len(videos) for videos in self.user_videos.values()),
            "timestamp": datetime.utcnow().isoformat()
        }


# 全局连接管理器实例
manager = ConnectionManager()


# ==================== Redis Pub/Sub 转发器 ====================

async def start_redis_listener(sio, redis_url: str):
    """
    启动 Redis Pub/Sub 监听器，将消息转发到 WebSocket 客户端

    Args:
        sio: Socket.IO AsyncServer实例
        redis_url: Redis 连接 URL
    """
    import redis.asyncio as aioredis

    async def listen():
        global _pubsub
        redis_client = aioredis.from_url(redis_url, decode_responses=True)
        pubsub = redis_client.pubsub()

        # 订阅转码进度频道
        await pubsub.subscribe("transcode:progress")
        await pubsub.subscribe("transcode:admin")
        await pubsub.subscribe("notifications:count")
        await pubsub.subscribe("upload:progress")
        await pubsub.subscribe("subtitle:progress")

        logger.info("Redis Pub/Sub listener started")

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    channel = message["channel"]
                    data = message["data"]

                    try:
                        import json
                        payload = json.loads(data)

                        if channel == "transcode:progress":
                            # 转发给特定用户
                            user_id = payload.get("user_id")
                            video_id = payload.get("video_id")
                            progress = payload.get("progress")
                            status = payload.get("status", "processing")

                            if user_id:
                                await manager.push_progress(
                                    sio=sio,
                                    user_id=str(user_id),
                                    video_id=str(video_id),
                                    progress=progress,
                                    status=status
                                )

                            # 同时广播给所有管理员（用于后台管理页面实时显示进度）
                            await manager.broadcast_transcode_update(
                                sio=sio,
                                event="transcode_progress",
                                data=payload
                            )

                        elif channel == "transcode:admin":
                            # 广播给所有管理员
                            await manager.broadcast_transcode_update(
                                sio=sio,
                                event=payload.get("event", "transcode_queue_changed"),
                                data=payload
                            )

                        elif channel == "notifications:count":
                            # 推送通知计数给特定用户
                            user_id = payload.get("user_id")
                            count = payload.get("count", 0)
                            if user_id:
                                await manager.push_notification_count(
                                    sio=sio,
                                    user_id=str(user_id),
                                    count=count
                                )

                        elif channel == "upload:progress":
                            # 推送上传进度给用户
                            user_id = payload.get("user_id")
                            msg_type = payload.get("type")
                            if user_id:
                                if msg_type == "upload_complete":
                                    await manager.push_upload_complete(
                                        sio=sio,
                                        user_id=str(user_id),
                                        session_id=payload.get("session_id", ""),
                                        video_id=payload.get("video_id", "")
                                    )
                                else:
                                    await manager.push_upload_progress(
                                        sio=sio,
                                        user_id=str(user_id),
                                        session_id=payload.get("session_id", ""),
                                        progress=payload.get("progress", 0),
                                        uploaded_chunks=payload.get("uploaded_chunks", 0),
                                        total_chunks=payload.get("total_chunks", 0)
                                    )

                        elif channel == "subtitle:progress":
                            # 推送字幕生成进度给用户
                            user_id = payload.get("user_id")
                            video_id = payload.get("video_id")
                            progress = payload.get("progress")
                            status = payload.get("status", "processing")
                            language = payload.get("language", "")

                            if user_id:
                                await manager.push_subtitle_progress(
                                    sio=sio,
                                    user_id=str(user_id),
                                    video_id=str(video_id),
                                    progress=progress,
                                    status=status,
                                    language=language
                                )

                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON in Redis message: {data}")
                    except Exception as e:
                        logger.error(f"Error processing Redis message: {e}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Redis listener error: {e}")
        finally:
            await pubsub.unsubscribe()
            await redis_client.close()

    # 启动 listen 作为后台任务
    asyncio.create_task(listen())
