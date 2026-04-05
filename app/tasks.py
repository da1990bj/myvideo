from celery import Celery
import os
import socket
import subprocess
import re
import json
import asyncio
import logging
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from uuid import UUID
from sqlmodel import Session, select
from database import engine
from data_models import Video, User, TranscodeTask
from config import settings, get_cold_storage_config, get_storage_migration_delay, get_transcode_config

logger = logging.getLogger(__name__)

# 配置 Celery
celery_app = Celery(
    "video_tasks",
    broker=settings.CELERY_BROKER,
    backend=settings.CELERY_BACKEND
)

celery_app.conf.update(
    task_track_started=True,
    result_expires=3600,
    task_serializer='json',
    accept_content=['json'],
)


def get_redis_client():
    """获取 Redis 客户端用于 Pub/Sub 和锁"""
    import redis
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def calculate_priority(user: User, waiting_hours: float = 0) -> tuple[int, str, str]:
    """
    计算转码任务优先级

    Args:
        user: 用户对象
        waiting_hours: 已等待小时数

    Returns:
        (priority_score, priority_type, queue_name)
    """
    config = get_transcode_config()
    aging_rate = config["aging_rate"]
    max_priority = config["max_priority"]
    vip_base = config["vip_base_priority"]
    paid_base = config["paid_base_priority"]

    # 判断用户类型和优先级
    if user.is_vip:
        # VIP用户：基础优先级 + aging
        base_priority = vip_base
        priority_type = "vip"
        queue_name = "vip"
        aging_bonus = min(waiting_hours * aging_rate * 0.6, 5)  # VIP aging 较慢
    else:
        # 普通用户：基础优先级0 + aging
        base_priority = 0
        priority_type = "normal"
        queue_name = "default"
        aging_bonus = min(waiting_hours * aging_rate, 10)  # 普通用户 aging 较快

    # 付费加速用户有最高优先级，不参与 aging
    # priority_type 为 paid_speedup 时，由 upgrade_priority API 调用时传入

    total_priority = min(base_priority + aging_bonus, max_priority)
    return int(total_priority), priority_type, queue_name


def create_transcode_task(video_id: str, user_id: str, priority_type: str = "normal") -> TranscodeTask:
    """
    创建转码任务记录

    Args:
        video_id: 视频ID
        user_id: 用户ID
        priority_type: 优先级类型 (normal, vip, vip_speedup, paid_speedup)

    Returns:
        TranscodeTask 对象
    """
    with Session(engine) as session:
        user = session.get(User, UUID(user_id))
        if not user:
            raise ValueError(f"User not found: {user_id}")

        # 计算优先级
        priority, p_type, queue_name = calculate_priority(user, 0)

        # 如果是加速类型，覆盖优先级
        if priority_type == "vip_speedup":
            config = get_transcode_config()
            priority = config["vip_base_priority"]
            p_type = "vip_speedup"
            queue_name = "priority"
        elif priority_type == "paid_speedup":
            config = get_transcode_config()
            priority = config["paid_base_priority"]
            p_type = "paid_speedup"
            queue_name = "priority"

        # 检查是否已有任务记录
        existing = session.exec(
            select(TranscodeTask).where(
                TranscodeTask.video_id == UUID(video_id),
                TranscodeTask.status.in_(["pending", "processing"])
            )
        ).first()

        if existing:
            # 更新已有任务
            existing.priority = priority
            existing.priority_type = p_type
            existing.queue_name = queue_name
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        # 创建新任务
        task = TranscodeTask(
            video_id=UUID(video_id),
            user_id=UUID(user_id),
            priority=priority,
            priority_type=p_type,
            queue_name=queue_name,
            status="pending"
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task


def update_transcode_task_priority(task_id: UUID, priority: int, priority_type: str, queue_name: str):
    """更新转码任务优先级"""
    with Session(engine) as session:
        task = session.get(TranscodeTask, task_id)
        if task:
            task.priority = priority
            task.priority_type = priority_type
            task.queue_name = queue_name
            session.add(task)
            session.commit()


def publish_transcode_progress(video_id: str, user_id: str, progress: int, status: str = "processing"):
    """
    通过 Redis Pub/Sub 推送转码进度

    Args:
        video_id: 视频ID
        user_id: 用户ID
        progress: 进度百分比
        status: 状态 (processing, completed, failed)
    """
    try:
        redis_client = get_redis_client()
        payload = json.dumps({
            "video_id": str(video_id),
            "user_id": str(user_id),
            "progress": progress,
            "status": status,
            "timestamp": datetime.utcnow().isoformat()
        })
        redis_client.publish("transcode:progress", payload)
    except Exception as e:
        logger.warning(f"Failed to publish transcode progress: {e}")


def broadcast_transcode_admin(event: str, data: dict):
    """
    通过 Redis Pub/Sub 广播转码事件给管理员

    Args:
        event: 事件名称
        data: 事件数据
    """
    try:
        redis_client = get_redis_client()
        payload = json.dumps({
            "event": event,
            **data
        })
        redis_client.publish("transcode:admin", payload)
    except Exception as e:
        logger.warning(f"Failed to broadcast transcode admin event: {e}")


def run_ffmpeg(cmd, video, session, start_percent, end_percent, total_duration, push_progress_callback=None, video_id: str = None):
    """
    辅助函数: 运行 FFmpeg 并更新进度

    Args:
        cmd: FFmpeg命令行
        video: Video对象
        session: 数据库会话
        start_percent: 本阶段的开始百分比
        end_percent: 本阶段的结束百分比
        total_duration: 视频总时长
        push_progress_callback: 进度回调函数 (video_id, progress) -> None
        video_id: 视频ID（用于保存时间戳到Redis）
    """
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    redis_client = get_redis_client()

    for line in process.stdout:
        if "out_time_ms=" in line:
            match = re.search(r"out_time_ms=(\d+)", line)
            if match:
                ms = int(match.group(1))
                sec = ms / 1000000.0
                if total_duration > 0:
                    # 计算当前阶段的进度 (0-100)
                    local_percent = min((sec / total_duration) * 100, 100)
                    # 映射到总进度 (start_percent -> end_percent)
                    global_percent = int(start_percent + (local_percent / 100) * (end_percent - start_percent))

                    if global_percent > video.progress and global_percent % 2 == 0:
                        video.progress = global_percent
                        session.add(video)
                        session.commit()

                        # 保存时间戳到Redis（用于暂停恢复）
                        if video_id:
                            redis_client.set(f"transcode_timestamp:{video_id}", f"{int(sec):02d}:{int(sec%60):02d}:{int((sec%1)*100):02d}", ex=3600)

                        # 触发进度推送回调（通过 Redis Pub/Sub）
                        if push_progress_callback:
                            try:
                                push_progress_callback(str(video.id), global_percent)
                            except Exception as e:
                                logger.warning(f"Error in progress callback: {e}")

    process.wait()
    if process.returncode != 0:
        raise Exception("FFmpeg error")


@celery_app.task(bind=True)
def transcode_video_task(self, video_id: str, priority_type: str = "normal", resume: str = None):
    """
    视频转码任务 (HLS 自适应码率)

    使用 Redis 分布式锁实现任务去重，防止同一视频重复转码。
    支持优先级队列：default, vip, priority (付费/VIP加速)
    支持暂停/恢复：resume="yes" 表示从暂停点恢复
    """
    redis_client = get_redis_client()
    lock_key = f"transcode_lock:{video_id}"

    # 检查是否是恢复任务
    is_resume = (resume == "resume")
    resume_percent = None
    resume_resolution = None
    resume_timestamp = None

    if is_resume:
        # 从Redis读取恢复信息
        resume_percent = redis_client.get(f"transcode_resume_percent:{video_id}")
        resume_resolution = redis_client.get(f"transcode_resume_resolution:{video_id}")
        resume_timestamp = redis_client.get(f"transcode_resume_timestamp:{video_id}")
        # 清理恢复信息
        redis_client.delete(f"transcode_resume_percent:{video_id}")
        redis_client.delete(f"transcode_resume_resolution:{video_id}")
        redis_client.delete(f"transcode_resume_timestamp:{video_id}")
        logger.info(f"Resuming video {video_id} from {resume_percent}% ({resume_resolution}, {resume_timestamp})")

    # 尝试获取分布式锁（防止同一视频重复转码）
    lock_acquired = redis_client.set(lock_key, self.request.id, nx=True, ex=3600)
    if not lock_acquired:
        # 检查是否已有任务在运行
        existing = redis_client.get(lock_key)
        logger.info(f"Video {video_id} already being transcoded by task {existing}, skipping")
        return {"status": "skipped", "reason": "already_transcoding", "task_id": existing}

    # 获取任务优先级（如果未传入 priority_type，默认 normal）
    task_record = None
    try:
        with Session(engine) as session:
            user = session.exec(select(Video).where(Video.id == video_id)).first()
            if user:
                task_record = create_transcode_task(video_id, str(user.user_id), priority_type)
    except Exception as e:
        logger.warning(f"Failed to create transcode task record: {e}")

    try:
        with Session(engine) as session:
            video = session.exec(select(Video).where(Video.id == video_id)).first()
            if not video:
                redis_client.delete(lock_key)
                if task_record:
                    task_record.status = "failed"
                    session.merge(task_record)
                    session.commit()
                return {"status": "error", "message": "Video not found"}

            video.status = "processing"
            video.progress = 0
            session.add(video)

            # 更新转码任务记录
            if task_record:
                task_record.status = "processing"
                task_record.started_at = datetime.utcnow()
                task_record.celery_task_id = self.request.id
                task_record.worker_name = socket.gethostname()
                session.merge(task_record)

            session.commit()

            # 广播转码队列更新给管理员
            broadcast_transcode_admin("transcode_queue_changed", {
                "video_id": str(video.id),
                "status": "processing",
                "title": video.title,
                "timestamp": datetime.utcnow().isoformat()
            })

            # 创建进度推送回调函数（通过 Redis）
            def push_progress_callback(video_id_str, progress):
                publish_transcode_progress(
                    video_id=video_id_str,
                    user_id=str(video.user_id),
                    progress=progress,
                    status="processing"
                )

            input_path = settings.fs_path(video.original_file_path)

            # HLS 输出目录
            base_dir = os.path.dirname(input_path).replace("uploads", "processed")
            hls_dir = os.path.join(base_dir, str(video.id))
            os.makedirs(hls_dir, exist_ok=True)

            # 封面目录
            thumb_dir = settings.THUMBNAILS_DIR
            os.makedirs(thumb_dir, exist_ok=True)
            thumb_path = str(thumb_dir / f"{video.id}.jpg")

            # 1. 获取总时长和分辨率
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-show_entries", "stream=width,height", "-of", "default=noprint_wrappers=1:nokey=1", input_path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )

            # Robust parsing using JSON
            probe_json = subprocess.run(
                 ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-show_entries", "stream=width,height", "-of", "json", input_path],
                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            data = json.loads(probe_json.stdout)
            total_duration = float(data['format']['duration'])

            video_stream = next((s for s in data.get('streams', []) if 'width' in s), None)
            if video_stream:
                width = int(video_stream['width'])
                height = int(video_stream['height'])
            else:
                width, height = 0, 0

            video.duration = int(total_duration)
            session.add(video)
            session.commit()

            # 2. 生成封面
            subprocess.run([
                "ffmpeg", "-i", input_path, "-ss", "00:00:05", "-vframes", "1", thumb_path, "-y"
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # 3. 自适应转码策略
            resolutions = [
                (1440, "6000k", "192k", "2k"),
                (1080, "4500k", "192k", "1080p"),
                (720, "2500k", "128k", "720p"),
                (480, "1000k", "96k", "480p")
            ]

            target_resolutions = []
            found_higher = False
            for r_h, r_b, r_a, r_n in resolutions:
                if height >= r_h * 0.9:
                    target_resolutions.append((r_h, r_b, r_a, r_n))
                    found_higher = True

            if not target_resolutions:
                target_resolutions.append((480, "1000k", "96k", "480p"))

            # 计算进度步长
            step = 95 / len(target_resolutions)
            current_progress_base = 0

            master_playlist = ["#EXTM3U", "#EXT-X-VERSION:3"]

            for r_h, r_b, r_a, r_n in target_resolutions:
                # 如果是恢复任务，跳过已完成的分辨率
                if is_resume and resume_resolution:
                    # 恢复时跳过比恢复点更高的分辨率
                    resolution_order = {"1440p": 0, "1080p": 1, "720p": 2, "480p": 3}
                    current_order = resolution_order.get(r_n, 99)
                    resume_order = resolution_order.get(resume_resolution, -1)
                    if current_order > resume_order:
                        # 跳过已完成的
                        current_progress_base += step
                        # Add to master anyway since file exists
                        if os.path.exists(os.path.join(hls_dir, f"{r_n}.m3u8")):
                            bw = int(r_b[:-1]) * 1000 + int(r_a[:-1]) * 1000
                            master_playlist.append(f'#EXT-X-STREAM-INF:BANDWIDTH={bw},RESOLUTION={int(r_h * width / height)}x{r_h},NAME="{r_n}"')
                            master_playlist.append(f"{r_n}.m3u8")
                        continue
                    elif current_order == resume_order and resume_timestamp:
                        # 从恢复点继续，使用 -ss 定位
                        cmd = [
                            "ffmpeg", "-ss", resume_timestamp, "-i", input_path,
                            "-c:v", "libx264", "-b:v", r_b, "-maxrate", r_b, "-bufsize", str(int(r_b[:-1])*2)+"k",
                            "-vf", f"scale=-2:{r_h}", "-c:a", "aac", "-b:a", r_a,
                            "-hls_time", "6", "-hls_playlist_type", "vod",
                            "-hls_segment_filename", os.path.join(hls_dir, f"{r_n}_%03d.ts"),
                            "-progress", "pipe:1",
                            os.path.join(hls_dir, f"{r_n}.m3u8"), "-y"
                        ]
                    else:
                        cmd = [
                            "ffmpeg", "-i", input_path,
                            "-c:v", "libx264", "-b:v", r_b, "-maxrate", r_b, "-bufsize", str(int(r_b[:-1])*2)+"k",
                            "-vf", f"scale=-2:{r_h}", "-c:a", "aac", "-b:a", r_a,
                            "-hls_time", "6", "-hls_playlist_type", "vod",
                            "-hls_segment_filename", os.path.join(hls_dir, f"{r_n}_%03d.ts"),
                            "-progress", "pipe:1",
                            os.path.join(hls_dir, f"{r_n}.m3u8"), "-y"
                        ]
                else:
                    cmd = [
                        "ffmpeg", "-i", input_path,
                        "-c:v", "libx264", "-b:v", r_b, "-maxrate", r_b, "-bufsize", str(int(r_b[:-1])*2)+"k",
                        "-vf", f"scale=-2:{r_h}", "-c:a", "aac", "-b:a", r_a,
                        "-hls_time", "6", "-hls_playlist_type", "vod",
                        "-hls_segment_filename", os.path.join(hls_dir, f"{r_n}_%03d.ts"),
                        "-progress", "pipe:1",
                        os.path.join(hls_dir, f"{r_n}.m3u8"), "-y"
                    ]

                # 记录当前处理的分辨率到Redis（用于暂停）
                redis_client.set(f"transcode_resolution:{video_id}", r_n, ex=3600)

                end_p = current_progress_base + step
                run_ffmpeg(cmd, video, session, current_progress_base, end_p, total_duration, push_progress_callback, video_id)
                current_progress_base = end_p

                # Add to master
                bw = int(r_b[:-1]) * 1000 + int(r_a[:-1]) * 1000
                master_playlist.append(f'#EXT-X-STREAM-INF:BANDWIDTH={bw},RESOLUTION={int(r_h * width / height)}x{r_h},NAME="{r_n}"')
                master_playlist.append(f"{r_n}.m3u8")

            # 5. 生成 Master Playlist
            master_path = os.path.join(hls_dir, "master.m3u8")
            with open(master_path, "w") as f:
                f.write('\n'.join(master_playlist))

            # 完成
            video.processed_file_path = f"/static/videos/processed/{video.id}/master.m3u8"
            video.thumbnail_path = f"/static/thumbnails/{video.id}.jpg"
            video.status = "completed"
            video.progress = 100

            # 更新转码任务记录
            if task_record:
                task_record.status = "completed"
                task_record.completed_at = datetime.utcnow()
                session.add(task_record)

            # 保存视频状态
            session.add(video)
            session.commit()

            # 广播转码完成
            broadcast_transcode_admin("transcode_queue_changed", {
                "video_id": str(video.id),
                "status": "completed",
                "title": video.title,
                "timestamp": datetime.utcnow().isoformat()
            })

            # 推送完成状态
            publish_transcode_progress(
                video_id=video_id,
                user_id=str(video.user_id),
                progress=100,
                status="completed"
            )

            return {"status": "completed", "video_id": str(video.id)}

    except Exception as e:
        # 标记任务失败
        with Session(engine) as session:
            video = session.exec(select(Video).where(Video.id == video_id)).first()
            if video:
                video.status = "failed"
                session.add(video)

            # 更新转码任务记录
            if task_record:
                task_record.status = "failed"
                task_record.completed_at = datetime.utcnow()
                session.add(task_record)

            session.commit()

        # 广播失败
        broadcast_transcode_admin("transcode_queue_changed", {
            "video_id": str(video_id),
            "status": "failed",
            "title": getattr(video, 'title', 'unknown') if 'video' in dir() else 'unknown',
            "timestamp": datetime.utcnow().isoformat()
        })

        # 推送失败状态
        try:
            video_id_str = str(video_id)
            user_id_str = str(video.user_id) if 'video' in dir() else ""
            if user_id_str:
                publish_transcode_progress(video_id_str, user_id_str, 0, "failed")
        except Exception:
            pass

        logger.error(f"Transcoding error for video {video_id}: {e}")
        return {"status": "error", "message": str(e)}

    finally:
        # 释放锁
        redis_client.delete(lock_key)


import random

@celery_app.task(bind=True)
def regenerate_thumbnail_task(self, video_id: str):
    """重新生成封面 (随机帧)"""
    with Session(engine) as session:
        video = session.get(Video, video_id)
        if not video:
            return {"status": "error", "message": "Video not found"}

        try:
            input_path = settings.fs_path(video.original_file_path)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            try:
                total_duration = float(probe.stdout.strip())
            except:
                total_duration = 10.0

            random_time = random.uniform(1, max(1, total_duration - 1))
            m, s = divmod(int(random_time), 60)
            h, m = divmod(m, 60)
            timestamp = f"{h:02d}:{m:02d}:{s:02d}"

            import time
            new_filename = f"{video.id}_{int(time.time())}.jpg"
            thumb_rel_path = f"/static/thumbnails/{new_filename}"
            thumb_abs_path = str(settings.THUMBNAILS_DIR / new_filename)

            old_thumb_path = settings.fs_path(video.thumbnail_path) if video.thumbnail_path else None
            if old_thumb_path and old_thumb_path.exists():
                try:
                    old_thumb_path.unlink()
                except:
                    pass

            subprocess.run([
                "ffmpeg", "-ss", timestamp, "-i", input_path, "-vframes", "1", thumb_abs_path, "-y"
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            video.thumbnail_path = thumb_rel_path
            session.add(video)
            session.commit()

            return {"status": "completed", "timestamp": timestamp}

        except Exception as e:
            logger.error(f"Error regenerating thumbnail for video {video_id}: {e}")
            return {"status": "error", "message": str(e)}


# ==================== 推荐系统定时任务 ====================

@celery_app.task(bind=True)
def compute_all_recommendation_scores(self):
    """
    计算所有活跃用户的推荐分数
    每天凌晨2点执行一次
    """
    from data_models import User
    from recommendation_engine import compute_user_recommendation_scores

    logger.info("Starting recommendation score computation...")

    try:
        with Session(engine) as session:
            active_users = session.exec(select(User).where(User.is_active == True)).all()
            logger.info(f"Found {len(active_users)} active users")

            processed_count = 0
            error_count = 0

            for user in active_users:
                try:
                    result = asyncio.run(
                        compute_user_recommendation_scores(session, user.id)
                    )
                    if result > 0:
                        processed_count += 1
                        logger.debug(f"Computed recommendations for user {user.username}")
                except Exception as e:
                    error_count += 1
                    logger.error(f"Error computing recommendations for user {user.id}: {e}")

            logger.info(f"Recommendation computation complete. Processed: {processed_count}, Errors: {error_count}")
            return {
                "status": "completed",
                "processed": processed_count,
                "errors": error_count,
                "total": len(active_users)
            }

    except Exception as e:
        logger.error(f"Critical error in recommendation computation: {e}")
        return {"status": "error", "message": str(e)}


# ==================== 存储迁移任务 ====================

@celery_app.task(bind=True)
def migrate_storage_task(self, old_dirs: dict, new_dirs: dict):
    """
    迁移视频文件到新目录
    """
    from time import sleep

    logger.info(f"Starting storage migration: {old_dirs} -> {new_dirs}")

    migration_delay = get_storage_migration_delay()
    logger.info(f"Migration delay between files: {migration_delay} seconds")

    migrated_count = 0
    failed_count = 0
    errors = []

    try:
        with Session(engine) as session:
            videos = session.exec(select(Video)).all()
            total = len(videos)
            logger.info(f"Found {total} videos to check for migration")

            for idx, video in enumerate(videos):
                try:
                    video_changed = False

                    # 迁移原始文件
                    if video.original_file_path and old_dirs.get("uploads") and new_dirs.get("uploads"):
                        old_path_str = video.original_file_path
                        if old_dirs["uploads"] in old_path_str:
                            new_path_str = old_path_str.replace(old_dirs["uploads"], new_dirs["uploads"])
                            old_path = Path(old_path_str)
                            new_path = Path(new_path_str)

                            if old_path.exists():
                                new_path.parent.mkdir(parents=True, exist_ok=True)
                                shutil.move(str(old_path), str(new_path))
                                video.original_file_path = new_path_str
                                video_changed = True
                                logger.info(f"Migrated original file: {old_path} -> {new_path}")

                    # 迁移转码文件目录
                    if video.processed_file_path and old_dirs.get("processed") and new_dirs.get("processed"):
                        old_path_str = video.processed_file_path
                        if old_dirs["processed"] in old_path_str:
                            new_path_str = old_path_str.replace(old_dirs["processed"], new_dirs["processed"])
                            old_dir = Path(old_path_str).parent
                            new_dir = Path(new_path_str).parent

                            if old_dir.exists() and old_dir != new_dir:
                                new_dir.mkdir(parents=True, exist_ok=True)
                                for item in old_dir.iterdir():
                                    shutil.move(str(item), str(new_dir / item.name))
                                try:
                                    old_dir.rmdir()
                                except OSError:
                                    pass
                                video.processed_file_path = new_path_str
                                video_changed = True
                                logger.info(f"Migrated processed directory: {old_dir} -> {new_dir}")

                    # 迁移缩略图
                    if video.thumbnail_path and old_dirs.get("thumbnails") and new_dirs.get("thumbnails"):
                        old_path_str = video.thumbnail_path
                        if old_dirs["thumbnails"] in old_path_str:
                            new_path_str = old_path_str.replace(old_dirs["thumbnails"], new_dirs["thumbnails"])
                            old_path = Path(old_path_str)
                            new_path = Path(new_path_str)

                            if old_path.exists():
                                new_path.parent.mkdir(parents=True, exist_ok=True)
                                shutil.move(str(old_path), str(new_path))
                                video.thumbnail_path = new_path_str
                                video_changed = True
                                logger.info(f"Migrated thumbnail: {old_path} -> {new_path}")

                    if video_changed:
                        session.add(video)
                        session.commit()
                        migrated_count += 1

                    if migration_delay > 0:
                        sleep(migration_delay)

                    if (idx + 1) % 5 == 0:
                        self.update_state(
                            state='PROGRESS',
                            meta={
                                'current': idx + 1,
                                'total': total,
                                'migrated': migrated_count,
                                'failed': failed_count,
                                'status': f'正在迁移: {idx + 1}/{total}'
                            }
                        )

                except Exception as e:
                    failed_count += 1
                    error_msg = f"Video {video.id}: {str(e)}"
                    errors.append(error_msg)
                    logger.error(f"Failed to migrate video {video.id}: {e}")
                    session.rollback()

            logger.info(f"Storage migration complete. Migrated: {migrated_count}, Failed: {failed_count}")
            return {
                "status": "completed",
                "migrated": migrated_count,
                "failed": failed_count,
                "errors": errors[:100]
            }

    except Exception as e:
        logger.error(f"Critical error in storage migration: {e}")
        return {"status": "error", "message": str(e)}


# ==================== 冷存储迁移定时任务 ====================

@celery_app.task(bind=True)
def cold_storage_migration_task(self):
    """
    定时检查并迁移冷存储视频
    条件：created_at 超过 COLD_STORAGE_TRIGGER_DAYS 天 AND views < COLD_STORAGE_TRIGGER_VIEWS
    """
    cold_config = get_cold_storage_config()
    if not cold_config["enabled"]:
        logger.info("Cold storage is disabled, skipping migration check")
        return {"status": "skipped", "reason": "cold_storage_disabled"}

    logger.info("Starting cold storage migration check...")

    try:
        with Session(engine) as session:
            days_threshold = cold_config["trigger_days"]
            views_threshold = cold_config["trigger_views"]
            cutoff_date = datetime.utcnow() - timedelta(days=days_threshold)

            candidates = session.exec(
                select(Video).where(
                    Video.is_cold == False,
                    Video.created_at < cutoff_date,
                    Video.views < views_threshold,
                    Video.status == "completed"
                )
            ).all()

            migrated_count = 0
            failed_count = 0

            for video in candidates:
                try:
                    original_path = settings.fs_path(video.original_file_path)
                    processed_path = settings.fs_path(video.processed_file_path) if video.processed_file_path else None

                    if original_path.exists():
                        cold_upload_dir = settings.COLD_STORAGE_UPLOADS_DIR
                        cold_upload_dir.mkdir(parents=True, exist_ok=True)

                        cold_file_path = cold_upload_dir / original_path.name
                        shutil.copy2(original_path, cold_file_path)
                        original_path.unlink()
                        logger.info(f"Migrated original file for video {video.id} to {cold_file_path}")

                    if processed_path and processed_path.exists():
                        cold_processed_dir = settings.COLD_STORAGE_PROCESSED_DIR
                        cold_processed_dir.mkdir(parents=True, exist_ok=True)

                        cold_processed_file = cold_processed_dir / processed_path.name
                        shutil.copy2(processed_path, cold_processed_file)
                        processed_path.unlink()
                        logger.info(f"Migrated processed file for video {video.id} to {cold_processed_file}")

                    video.is_cold = True
                    video.cold_stored_at = datetime.utcnow()
                    session.add(video)
                    session.commit()

                    migrated_count += 1
                    logger.info(f"Video {video.id} migrated to cold storage")

                except Exception as e:
                    failed_count += 1
                    logger.error(f"Failed to migrate video {video.id}: {e}")
                    session.rollback()

            logger.info(f"Cold storage migration complete. Migrated: {migrated_count}, Failed: {failed_count}")
            return {
                "status": "completed",
                "migrated": migrated_count,
                "failed": failed_count,
                "candidates_checked": len(candidates)
            }

    except Exception as e:
        logger.error(f"Critical error in cold storage migration: {e}")
        return {"status": "error", "message": str(e)}


# ==================== 转码队列优先级 Aging 任务 ====================

@celery_app.task(bind=True)
def update_transcode_aging(self):
    """
    定时更新转码任务的等待时间并重新计算优先级
    每小时执行一次，使普通用户的任务优先级随等待时间增长
    """
    logger.info("Starting transcode aging update...")

    try:
        with Session(engine) as session:
            # 获取所有 pending 状态的任务
            pending_tasks = session.exec(
                select(TranscodeTask).where(TranscodeTask.status == "pending")
            ).all()

            updated_count = 0
            for task in pending_tasks:
                # 增加等待时间
                task.waiting_hours += 1

                # 获取用户信息
                user = session.get(User, task.user_id)
                if not user:
                    continue

                # 计算新的优先级
                priority, priority_type, queue_name = calculate_priority(user, task.waiting_hours)

                # 如果是加速类型，跳过 aging 更新
                if task.priority_type in ("vip_speedup", "paid_speedup"):
                    session.add(task)
                    continue

                # 只有普通用户和普通VIP任务需要 aging
                if priority != task.priority or queue_name != task.queue_name:
                    old_priority = task.priority
                    task.priority = priority
                    task.queue_name = queue_name
                    task.priority_type = priority_type
                    session.add(task)
                    updated_count += 1
                    logger.debug(f"Task {task.id}: priority {old_priority} -> {priority}, queue {task.queue_name}")
                else:
                    session.add(task)

            session.commit()
            logger.info(f"Transcode aging update complete. Updated {updated_count}/{len(pending_tasks)} tasks")

            return {
                "status": "completed",
                "total": len(pending_tasks),
                "updated": updated_count
            }

    except Exception as e:
        logger.error(f"Error in transcode aging update: {e}")
        return {"status": "error", "message": str(e)}


# Celery Beat 定时任务配置
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    'compute-recommendations-daily': {
        'task': 'tasks.compute_all_recommendation_scores',
        'schedule': crontab(hour=2, minute=0),
        'options': {'queue': 'default'}
    },
    'cold-storage-migration-daily': {
        'task': 'tasks.cold_storage_migration_task',
        'schedule': crontab(hour=1, minute=0),
        'options': {'queue': 'default'}
    },
    'transcode-aging-hourly': {
        'task': 'tasks.update_transcode_aging',
        'schedule': crontab(minute=0),  # 每小时执行一次
        'options': {'queue': 'default'}
    },
}
