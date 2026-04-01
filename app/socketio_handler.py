"""
WebSocket 处理器 - 使用 python-socketio 实现实时转码进度推送
"""

from typing import Dict, Optional
from uuid import UUID
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    用户连接池管理器
    - 维护在线用户与WebSocket会话的映射
    - 处理连接/断开事件
    - 推送转码进度更新
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

        # 返回连接确认信息
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
                [
                    {'video_id': '...', 'progress': 50, 'status': 'processing'},
                    ...
                ]
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
        """
        获取连接池统计信息
        用于监控和调试
        """
        return {
            "connected_users": self.get_connected_users_count(),
            "total_processing_videos": sum(len(videos) for videos in self.user_videos.values()),
            "timestamp": datetime.utcnow().isoformat()
        }


# 全局连接管理器实例
manager = ConnectionManager()
