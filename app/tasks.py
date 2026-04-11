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
from typing import List
from sqlmodel import Session, select
from database import engine
from data_models import Video, User, TranscodeTask, SystemConfig
from config import settings, get_cold_storage_config, get_storage_migration_delay, get_transcode_config, reload_runtime_config
from dependencies import log_admin_action

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

        # 检查视频状态，如果已完成则不再创建新任务
        video = session.get(Video, UUID(video_id))
        if video and video.status == "completed":
            # 视频已转码完成，检查是否已有任务记录
            existing = session.exec(
                select(TranscodeTask).where(
                    TranscodeTask.video_id == UUID(video_id),
                    TranscodeTask.status == "completed"
                )
            ).first()
            if existing:
                logger.info(f"Video {video_id} already completed, reusing task {existing.id}")
                return existing
            # 视频已完成但无任务记录，不创建新任务
            logger.info(f"Video {video_id} already completed, skipping task creation")
            return None

        # 检查是否已有 pending/processing/paused 任务
        existing = session.exec(
            select(TranscodeTask).where(
                TranscodeTask.video_id == UUID(video_id),
                TranscodeTask.status.in_(["pending", "processing", "paused"])
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

    # 先创建任务记录（这样队列立即可见）
    task_record = None
    video_status = None
    try:
        with Session(engine) as session:
            video = session.exec(select(Video).where(Video.id == video_id)).first()
            if video:
                video_status = video.status
                task_record = create_transcode_task(video_id, str(video.user_id), priority_type)
    except Exception as e:
        logger.warning(f"Failed to create transcode task record: {e}")

    # 如果任务记录为 None（视频已完成），则跳过
    if task_record is None and video_status == "completed":
        logger.info(f"Video {video_id} already completed, skipping transcode")
        return {"status": "skipped", "reason": "video_already_completed"}

    # 如果视频状态为暂停，则跳过处理
    if video_status == "paused":
        logger.info(f"Video {video_id} is paused, skipping transcode")
        return {"status": "skipped", "reason": "video_paused"}

    # 检查是否已有正在运行的 ffmpeg 进程（防止僵尸进程重复启动）
    import subprocess
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"ffmpeg.*/{video_id}/"],
            capture_output=True, text=True
        )
        if result.stdout.strip():
            existing_pids = result.stdout.strip().split('\n')
            logger.warning(f"Video {video_id} already has ffmpeg running: {existing_pids}")
            # 更新任务状态为 failed
            if task_record:
                task_record.status = "failed"
                task_record.error_message = f"Already running: ffmpeg process {existing_pids[0]}"
                with Session(engine) as session:
                    session.merge(task_record)
                    session.commit()
            return {"status": "skipped", "reason": "ffmpeg_already_running", "pids": existing_pids}
    except Exception as e:
        logger.warning(f"Failed to check existing ffmpeg: {e}")

    # 尝试获取分布式锁（防止同一视频重复转码）
    # 使用nx=True和无限期等待，直到获取锁为止
    lock_acquired = False
    for attempt in range(3):
        lock_acquired = redis_client.set(lock_key, self.request.id, nx=True, ex=7200)
        if lock_acquired:
            break
        # 检查锁是否过期但进程还在跑
        existing = redis_client.get(lock_key)
        if existing:
            logger.warning(f"Lock held by {existing}, waiting...")
            import time
            time.sleep(2)
        else:
            # 锁已过期但Redis key不存在，尝试重新获取
            pass

    if not lock_acquired:
        existing = redis_client.get(lock_key)
        logger.info(f"Video {video_id} already being transcoded by task {existing}, skipping")
        if task_record:
            task_record.status = "failed"
            task_record.error_message = f"Lock held by: {existing}"
            with Session(engine) as session:
                session.merge(task_record)
                session.commit()
        return {"status": "skipped", "reason": "already_transcoding", "task_id": existing}

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
            # 恢复时不重置进度，使用保存的值
            if is_resume and resume_percent is not None:
                video.progress = float(resume_percent)
            elif not is_resume:
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

            # 3. 自适应转码策略 - 根据原分辨率动态生成
            resolutions = [
                (1440, "6000k", "192k", "2k"),
                (1080, "4500k", "192k", "1080p"),
                (720, "2500k", "128k", "720p"),
                (480, "1000k", "96k", "480p"),
                (360, "600k", "64k", "360p"),
            ]

            # 找出原视频高度对应的分辨率
            target_resolutions = []
            original_res_idx = None

            for i, (r_h, r_b, r_a, r_n) in enumerate(resolutions):
                if height >= r_h:
                    # 原视频高度 >= 当前分辨率，使用原高度作为分辨率
                    # 按比例计算码率
                    scale = height / r_h
                    video_bitrate = f"{int(int(r_b[:-1]) * scale)}k"
                    target_resolutions.append((height, video_bitrate, r_a, f"{height}p"))
                    original_res_idx = i
                    break

            if original_res_idx is None:
                # 原视频高度不在列表中，找最接近的较低分辨率
                for i, (r_h, r_b, r_a, r_n) in enumerate(resolutions):
                    if height < r_h:
                        target_resolutions.append((r_h, r_b, r_a, r_n))
                        original_res_idx = i
                        break

            # 添加低一级、低两级、低三级（最多4个，最低360p）
            for i in range(original_res_idx + 1, min(original_res_idx + 4, len(resolutions))):
                r_h, r_b, r_a, r_n = resolutions[i]
                target_resolutions.append((r_h, r_b, r_a, r_n))

            # 计算进度步长
            step = 95 / len(target_resolutions)
            current_progress_base = 0

            master_playlist = ["#EXTM3U", "#EXT-X-VERSION:3"]

            for r_h, r_b, r_a, r_n in target_resolutions:
                # 如果是恢复任务，跳过已完成的分辨率
                if is_resume and resume_resolution:
                    # 恢复时跳过比恢复点更早完成的分辨率（order更小的）
                    # 处理顺序：1440p(0) → 1080p(1) → 720p(2) → 480p(3)
                    # 如果暂停在1080p，1440p已完成，1080p要恢复，720p和480p还未处理
                    resolution_order = {"1440p": 0, "1080p": 1, "720p": 2, "480p": 3}
                    current_order = resolution_order.get(r_n, 99)
                    resume_order = resolution_order.get(resume_resolution, -1)
                    if current_order < resume_order:
                        # 比恢复点更早的分辨率（order更小），已完成的，跳过但添加到master
                        m3u8_path = os.path.join(hls_dir, f"{r_n}.m3u8")
                        if os.path.exists(m3u8_path) and os.path.getsize(m3u8_path) > 100:
                            current_progress_base += step
                            bw = int(r_b[:-1]) * 1000 + int(r_a[:-1]) * 1000
                            master_playlist.append(f'#EXT-X-STREAM-INF:BANDWIDTH={bw},RESOLUTION={int(r_h * width / height)}x{r_h},NAME="{r_n}"')
                            master_playlist.append(f"{r_n}.m3u8")
                        continue
                    elif current_order == resume_order and resume_timestamp:
                        # 从恢复点继续，使用 -ss 定位
                        cmd = [
                            "ffmpeg", "-ss", resume_timestamp, "-i", input_path,
                            "-map", "0:v:0", "-map", "0:a:0",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level:v", "4.1",
                            "-b:v", r_b, "-maxrate", r_b, "-bufsize", str(int(r_b[:-1])*2)+"k",
                            "-vf", f"scale=-2:{r_h},format=yuv420p",
                            "-c:a", "aac", "-b:a", r_a, "-af", "aformat=channel_layouts=stereo",
                            "-hls_time", "6", "-hls_playlist_type", "vod",
                            "-hls_segment_filename", os.path.join(hls_dir, f"{r_n}_%03d.ts"),
                            "-progress", "pipe:1",
                            os.path.join(hls_dir, f"{r_n}.m3u8"), "-y"
                        ]
                    else:
                        cmd = [
                            "ffmpeg", "-i", input_path,
                            "-map", "0:v:0", "-map", "0:a:0",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level:v", "4.1",
                            "-b:v", r_b, "-maxrate", r_b, "-bufsize", str(int(r_b[:-1])*2)+"k",
                            "-vf", f"scale=-2:{r_h},format=yuv420p",
                            "-c:a", "aac", "-b:a", r_a, "-af", "aformat=channel_layouts=stereo",
                            "-hls_time", "6", "-hls_playlist_type", "vod",
                            "-hls_segment_filename", os.path.join(hls_dir, f"{r_n}_%03d.ts"),
                            "-progress", "pipe:1",
                            os.path.join(hls_dir, f"{r_n}.m3u8"), "-y"
                        ]
                else:
                    cmd = [
                        "ffmpeg", "-i", input_path,
                        "-map", "0:v:0", "-map", "0:a:0",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level:v", "4.1",
                        "-b:v", r_b, "-maxrate", r_b, "-bufsize", str(int(r_b[:-1])*2)+"k",
                        "-vf", f"scale=-2:{r_h},format=yuv420p",
                        "-c:a", "aac", "-b:a", r_a, "-af", "aformat=channel_layouts=stereo",
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

            # 6. 提取字幕流
            try:
                extracted_languages = extract_subtitle_streams(video_id, input_path, hls_dir)
                if extracted_languages:
                    update_master_playlist_with_subtitles(video_id, extracted_languages)
                    # 更新视频的字幕语言列表
                    video.subtitle_languages = extracted_languages
                    logger.info(f"Extracted subtitles for video {video_id}: {extracted_languages}")
            except Exception as e:
                logger.warning(f"Failed to extract subtitles for video {video_id}: {e}")

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
        video_title = None
        video_user_id = None
        with Session(engine) as session:
            video = session.exec(select(Video).where(Video.id == video_id)).first()
            if video:
                video_title = video.title
                video_user_id = str(video.user_id)
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
            "title": video_title or 'unknown',
            "timestamp": datetime.utcnow().isoformat()
        })

        # 推送失败状态
        try:
            if video_user_id:
                publish_transcode_progress(str(video_id), video_user_id, 0, "failed")
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
            new_filename = f"{video.id}.jpg"
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


# ==================== 热门统计任务 ====================

@celery_app.task(bind=True)
def compute_daily_trending_task(self):
    """
    计算每日热门视频，存数据库
    每天凌晨2点执行
    """
    from data_models import Video, DailyTrendingVideo
    from cache_manager import get_cache
    from datetime import date

    logger.info("Starting daily trending computation...")

    try:
        cache = get_cache()
        today = date.today()

        with Session(engine) as session:
            # 获取有效视频（未删除、已审核、已完成、公开）
            videos = session.exec(
                select(Video).where(
                    Video.is_deleted == False,
                    Video.is_approved == "approved",
                    Video.status == "completed",
                    Video.visibility == "public"
                )
            ).all()

            trending_data = []
            for v in videos:
                # 计算热度分数
                score = (v.views ** 0.5) + v.like_count * 2 + v.favorite_count * 3

                # 存 Redis 全局热门
                cache.zadd_trending(str(v.id), score)

                # 存数据库
                trending = DailyTrendingVideo(
                    video_id=v.id,
                    trending_date=today,
                    score=float(score),
                    views=v.views or 0,
                    likes=v.like_count or 0,
                    favorites=v.favorite_count or 0
                )
                trending_data.append(trending)

            # 批量写入数据库
            for t in trending_data:
                session.add(t)
            session.commit()

            logger.info(f"Daily trending computation complete. Processed: {len(trending_data)} videos")
            return {"status": "completed", "processed": len(trending_data)}

    except Exception as e:
        logger.error(f"Critical error in daily trending computation: {e}")
        return {"status": "error", "message": str(e)}


@celery_app.task(bind=True)
def compute_category_trending_task(self):
    """
    计算分类热门视频，存数据库
    每天凌晨3点执行
    """
    from data_models import Video, Category, CategoryTrendingVideo
    from cache_manager import get_cache
    from datetime import date

    logger.info("Starting category trending computation...")

    try:
        cache = get_cache()
        today = date.today()

        with Session(engine) as session:
            # 获取所有分类
            categories = session.exec(select(Category)).all()

            total_processed = 0
            for cat in categories:
                # 获取该分类的有效视频
                videos = session.exec(
                    select(Video).where(
                        Video.category_id == cat.id,
                        Video.is_deleted == False,
                        Video.is_approved == "approved",
                        Video.status == "completed"
                    )
                ).all()

                for v in videos:
                    # 计算热度分数
                    score = (v.views ** 0.5) + v.like_count * 2 + v.favorite_count * 3

                    # 存 Redis 分类热门
                    cache.zadd_trending_category(cat.id, str(v.id), score)

                    # 存数据库
                    trending = CategoryTrendingVideo(
                        category_id=cat.id,
                        video_id=v.id,
                        trending_date=today,
                        score=float(score)
                    )
                    session.add(trending)

                total_processed += len(videos)

            session.commit()
            logger.info(f"Category trending computation complete. Processed: {total_processed} videos")
            return {"status": "completed", "processed": total_processed}

    except Exception as e:
        logger.error(f"Critical error in category trending computation: {e}")
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
            videos = session.exec(select(Video).where(Video.is_deleted == False)).all()
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
    定时扫描冷存储可迁移视频（仅扫描，不执行迁移）
    条件：created_at 超过 COLD_STORAGE_TRIGGER_DAYS 天 AND views < COLD_STORAGE_TRIGGER_VIEWS
    """
    cold_config = get_cold_storage_config()
    if not cold_config["enabled"]:
        logger.info("Cold storage is disabled, skipping migration check")
        return {"status": "skipped", "reason": "cold_storage_disabled"}

    logger.info("Starting cold storage migration scan (preview only)...")

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
                    Video.status == "completed",
                    Video.is_approved == "approved"
                )
            ).all()

            # 仅扫描预览，不执行迁移
            total_size = 0
            candidate_list = []
            for video in candidates:
                try:
                    original_path = settings.fs_path(video.original_file_path)
                    if original_path.exists():
                        total_size += original_path.stat().st_size
                    candidate_list.append({
                        "video_id": str(video.id),
                        "title": video.title,
                        "views": video.views,
                        "created_at": video.created_at.isoformat() if video.created_at else None,
                        "file_size": original_path.stat().st_size if original_path.exists() else 0
                    })
                except Exception:
                    pass

            logger.info(f"Cold storage scan complete. Found {len(candidates)} candidates, total size: {total_size}")
            return {
                "status": "completed",
                "action": "scan_only",
                "candidates_count": len(candidates),
                "total_size": total_size,
                "candidates": candidate_list
            }

    except Exception as e:
        logger.error(f"Critical error in cold storage scan: {e}")
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


@celery_app.task(bind=True)
def cleanup_zombie_ffmpeg_task(self):
    """
    定时清理僵尸 ffmpeg 进程和过期锁
    每10分钟执行一次
    """
    import subprocess

    logger.info("Starting zombie ffmpeg cleanup...")

    try:
        # 1. 获取所有 TranscodeTask 中 status=processing 但过期的任务
        with Session(engine) as session:
            stale_threshold = datetime.utcnow() - timedelta(minutes=30)  # 超过30分钟视为过期
            stale_tasks = session.exec(
                select(TranscodeTask).where(
                    TranscodeTask.status == "processing",
                    TranscodeTask.started_at < stale_threshold
                )
            ).all()

            cleaned = 0
            for task in stale_tasks:
                # 检查是否有对应的 ffmpeg 进程
                try:
                    result = subprocess.run(
                        ["pgrep", "-f", f"ffmpeg.*{task.video_id}"],
                        capture_output=True, text=True
                    )
                    pids = result.stdout.strip().split('\n') if result.stdout.strip() else []

                    if not pids or pids == ['']:
                        # 没有进程，更新任务状态
                        task.status = "failed"
                        task.error_message = "Task timeout: no ffmpeg process found"
                        task.completed_at = datetime.utcnow()
                        session.add(task)
                        cleaned += 1
                        logger.info(f"Cleaned up stale task {task.id} for video {task.video_id}")
                    else:
                        # 有进程还在跑，可能是慢速转码，刷新时间
                        task.started_at = datetime.utcnow()
                        session.add(task)
                        logger.info(f"Task {task.id} still running, refreshed timestamp")

                except Exception as e:
                    logger.warning(f"Error checking ffmpeg for task {task.id}: {e}")

            session.commit()
            logger.info(f"Zombie cleanup complete. Cleaned {cleaned} stale tasks")

            # 2. 清理过期的 Redis 锁（超过2小时）
            redis_client = get_redis_client()
            lock_keys = redis_client.keys("transcode_lock:*")
            for key in lock_keys:
                ttl = redis_client.ttl(key)
                if ttl == -1:  # 没有过期时间，可能是遗留锁
                    # 检查对应视频是否有 processing 任务
                    video_id = key.replace("transcode_lock:", "")
                    task = session.exec(
                        select(TranscodeTask).where(
                            TranscodeTask.video_id == video_id,
                            TranscodeTask.status == "processing"
                        )
                    ).first()
                    if not task:
                        redis_client.delete(key)
                        logger.info(f"Deleted stale lock: {key}")

            return {"status": "completed", "cleaned": cleaned}

    except Exception as e:
        logger.error(f"Error in zombie cleanup: {e}")
        return {"status": "error", "message": str(e)}


# ==================== 存储目录迁移任务 ====================

@celery_app.task(bind=True)
def migrate_storage_task(self, dir_type: str, source_subdir: str, admin_id: str,
                        concurrency: int = 2, max_speed_mbps: float = 0):
    """
    异步迁移存储目录（支持并发和速度限制）
    dir_type: uploads | processed | thumbnails
    source_subdir: 源目录相对路径
    concurrency: 并发数，默认2
    max_speed_mbps: 最大速度限制(MB/s)，0表示不限制
    """
    import json
    import shutil
    import time
    from sqlmodel import select
    from concurrent.futures import ThreadPoolExecutor, as_completed

    logger.info(f"Starting storage migration: {dir_type} from {source_subdir}, concurrency={concurrency}, max_speed={max_speed_mbps}MB/s")

    source_dir = settings.BASE_DIR / source_subdir
    target_dir = settings.UPLOADS_DIR if dir_type == "uploads" else (
        settings.PROCESSED_DIR if dir_type == "processed" else settings.THUMBNAILS_DIR
    )

    if not source_dir.exists():
        return {"status": "error", "message": f"Source directory does not exist: {source_dir}"}

    # 确定文件扩展名模式
    if dir_type == "uploads":
        pattern = "*.mp4"
    elif dir_type == "processed":
        pattern = "*.m3u8"
    else:
        pattern = "*.jpg"

    # 收集所有要迁移的文件（含大小）
    files_to_migrate = []
    for f in source_dir.rglob(pattern):
        if f.is_file():
            rel_path = f.relative_to(source_dir)
            try:
                file_size = f.stat().st_size
            except Exception:
                file_size = 0
            files_to_migrate.append({
                "source": str(f),
                "rel_path": str(rel_path),
                "size": file_size,
            })

    total = len(files_to_migrate)
    if total == 0:
        return {"status": "completed", "migrated": 0, "failed": 0, "message": "No files to migrate"}

    # 创建目标目录
    target_dir.mkdir(parents=True, exist_ok=True)

    # 路径前缀
    old_prefix = f"/data/myvideo/{source_subdir}"
    new_subdir = settings.UPLOADS_SUBDIR if dir_type == "uploads" else (
        settings.PROCESSED_SUBDIR if dir_type == "processed" else settings.THUMBNAILS_SUBDIR
    )
    new_prefix = f"/data/myvideo/{new_subdir}"

    migrated = 0
    failed = 0
    total_bytes = 0
    start_time = time.time()

    def migrate_single_file(file_info, video_path_map):
        """迁移单个文件"""
        nonlocal migrated, failed, total_bytes
        try:
            source_path = Path(file_info["source"])
            target_path = target_dir / file_info["rel_path"]

            # 确保目标子目录存在
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # 移动文件
            shutil.move(str(source_path), str(target_path))
            file_size = file_info["size"]
            total_bytes += file_size

            # 速度限制
            if max_speed_mbps > 0:
                elapsed = time.time() - start_time
                expected_bytes = elapsed * max_speed_mbps * 1024 * 1024
                if total_bytes > expected_bytes:
                    sleep_time = (total_bytes - expected_bytes) / (max_speed_mbps * 1024 * 1024)
                    if sleep_time > 0:
                        time.sleep(sleep_time)

            # 更新数据库中匹配的视频路径
            with Session(engine) as session:
                for video_id, old_path in video_path_map.items():
                    if old_path and file_info["rel_path"] in old_path:
                        new_path = old_path.replace(old_prefix, new_prefix, 1)
                        video = session.get(Video, video_id)
                        if video:
                            if dir_type == "uploads":
                                video.original_file_path = new_path
                            elif dir_type == "processed":
                                video.processed_file_path = new_path
                            else:
                                video.thumbnail_path = new_path
                            session.add(video)
                            session.commit()
                            logger.info(f"Updated video {video_id} path: {old_path} -> {new_path}")

            return {"status": "success", "file": file_info["rel_path"], "size": file_size}
        except Exception as e:
            logger.error(f"Failed to migrate {file_info['source']}: {e}")
            return {"status": "failed", "file": file_info["rel_path"], "error": str(e)}

    # 获取需要更新路径的视频映射
    with Session(engine) as session:
        if dir_type == "uploads":
            path_field = Video.original_file_path
        elif dir_type == "processed":
            path_field = Video.processed_file_path
        else:
            path_field = Video.thumbnail_path

        videos_to_check = session.exec(select(Video).where(path_field.startswith(old_prefix))).all()
        video_path_map = {}
        for v in videos_to_check:
            if dir_type == "uploads":
                video_path_map[str(v.id)] = v.original_file_path
            elif dir_type == "processed":
                video_path_map[str(v.id)] = v.processed_file_path
            else:
                video_path_map[str(v.id)] = v.thumbnail_path

    # 使用线程池并发迁移
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(migrate_single_file, f, video_path_map): f for f in files_to_migrate}
        completed = 0

        for future in as_completed(futures):
            result = future.result()
            completed += 1

            if result["status"] == "success":
                migrated += 1
            else:
                failed += 1

            # 更新进度
            self.update_state(
                state='PROGRESS',
                meta={
                    'current': completed,
                    'total': total,
                    'migrated': migrated,
                    'failed': failed,
                    'current_file': result["file"],
                    'status': f'迁移中: {completed}/{total}'
                }
            )

    # 从历史中移除已迁移的目录
    with Session(engine) as session:
        history_key = f"{dir_type.upper()}_HISTORY"
        history_value = get_runtime_config(history_key, "[]")
        try:
            history_list = json.loads(history_value)
            if source_subdir in history_list:
                history_list.remove(source_subdir)
                conf = session.exec(select(SystemConfig).where(SystemConfig.key == history_key)).first()
                if conf:
                    conf.value = json.dumps(history_list)
                    session.add(conf)
                    session.commit()
                    reload_runtime_config()
        except Exception as e:
            logger.error(f"Failed to update history: {e}")

        log_admin_action(session, admin_id, "migrate_storage_complete", dir_type,
                        f"Migrated {migrated} files from {source_subdir}")

    # 删除空源目录
    try:
        shutil.rmtree(source_dir)
    except Exception:
        pass

    logger.info(f"Storage migration complete. Migrated: {migrated}, Failed: {failed}")
    return {
        "status": "completed",
        "migrated": migrated,
        "failed": failed,
        "source": str(source_dir),
        "target": str(target_dir),
    }


# ============ 字幕生成任务 ============

def format_timestamp(seconds: float) -> str:
    """Format seconds to VTT timestamp (HH:MM:SS.mmm)"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def publish_subtitle_progress(video_id: str, user_id: str, progress: int, status: str = "processing", language: str = ""):
    """通过 Redis Pub/Sub 推送字幕生成进度"""
    try:
        redis_client = get_redis_client()
        import json
        payload = json.dumps({
            "user_id": user_id,
            "video_id": video_id,
            "progress": progress,
            "status": status,
            "language": language,
            "timestamp": datetime.utcnow().isoformat()
        })
        redis_client.publish("subtitle:progress", payload)
    except Exception as e:
        logger.warning(f"Failed to publish subtitle progress: {e}")


def extract_subtitle_streams(video_id: str, input_path: str, hls_dir: str) -> List[str]:
    """
    从原始视频中提取字幕流并转换为 WebVTT 格式
    返回提取成功的语言列表
    支持多种字幕格式: subrip(srt), ass, ssa, webvtt, mov_text 等
    """
    try:
        # 使用 ffprobe 获取字幕流信息（包括tags用于提取language）
        probe_result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "stream=index,codec_name,codec_type,language,Disposition,Tags",
             "-select_streams", "s",
             "-of", "json",
             input_path],
            capture_output=True, text=True
        )

        if not probe_result.stdout:
            return []

        probe_data = json.loads(probe_result.stdout)
        streams = probe_data.get("streams", [])

        if not streams:
            return []

        # 创建字幕目录
        subtitle_dir = Path(hls_dir) / "subtitles"
        subtitle_dir.mkdir(parents=True, exist_ok=True)

        extracted_languages = []

        for stream in streams:
            try:
                stream_index = stream.get("index")
                codec_name = stream.get("codec_name")

                # 优先从 language 字段获取语言代码，否则从 tags.title 提取
                language = stream.get("language")
                if not language or language == "und":
                    # 尝试从 tags.title 提取语言信息
                    tags = stream.get("tags") or {}
                    title = tags.get("title", "")
                    # title 格式如 "English (forced)", "العربية", "English (SDH)"
                    # 提取第一个单词作为语言标识
                    if title:
                        language = title.split(" ")[0].lower()
                    else:
                        language = "unknown"

                # 跳过非字幕流
                if stream.get("codec_type") != "subtitle":
                    continue

                # 使用 stream_index 命名字幕文件，避免覆盖
                lang_key = f"track{stream_index}_{language}"

                # 转换后的 VTT 文件路径（使用 lang_key 避免文件名冲突）
                vtt_path = subtitle_dir / f"{lang_key}.vtt"

                # 纯净的语言代码（用于返回和播放列表）
                # 如果检测到未知语言但有 stream_index，使用 stream_index 作为后备标识
                clean_language = language if language != "unknown" else f"track{stream_index}"
                extracted_languages.append(clean_language)

                # 根据不同字幕格式选择转换方式
                if codec_name == "subrip":
                    # subrip (srt) 转换为 webvtt
                    extract_result = subprocess.run(
                        ["ffmpeg", "-i", input_path,
                         "-map", f"0:s:{stream_index}",
                         "-c:s", "webvtt",
                         str(vtt_path), "-y"],
                        capture_output=True, text=True
                    )
                elif codec_name in ("ass", "ssa"):
                    # ass/ssa 格式先转 srt 再转 webvtt（ffmpeg 直接转 ass 到 webvtt 可能丢失样式）
                    srt_path = subtitle_dir / f"{lang_key}.srt"
                    extract_result = subprocess.run(
                        ["ffmpeg", "-i", input_path,
                         "-map", f"0:s:{stream_index}",
                         "-c:s", "subrip",
                         str(srt_path), "-y"],
                        capture_output=True, text=True
                    )
                    if extract_result.returncode == 0 and srt_path.exists():
                        # 再转 webvtt
                        extract_result = subprocess.run(
                            ["ffmpeg", "-i", str(srt_path),
                             "-c:s", "webvtt",
                             str(vtt_path), "-y"],
                            capture_output=True, text=True
                        )
                        # 清理中间文件
                        try:
                            srt_path.unlink()
                        except Exception:
                            pass
                elif codec_name == "webvtt":
                    # webvtt 直接复制
                    extract_result = subprocess.run(
                        ["ffmpeg", "-i", input_path,
                         "-map", f"0:s:{stream_index}",
                         "-c:s", "webvtt",
                         str(vtt_path), "-y"],
                        capture_output=True, text=True
                    )
                elif codec_name == "mov_text":
                    # mov_text (常见于 mp4) 转换为 webvtt
                    extract_result = subprocess.run(
                        ["ffmpeg", "-i", input_path,
                         "-map", f"0:s:{stream_index}",
                         "-c:s", "webvtt",
                         str(vtt_path), "-y"],
                        capture_output=True, text=True
                    )
                else:
                    # 尝试直接转换
                    extract_result = subprocess.run(
                        ["ffmpeg", "-i", input_path,
                         "-map", f"0:s:{stream_index}",
                         "-c:s", "webvtt",
                         str(vtt_path), "-y"],
                        capture_output=True, text=True
                    )

                if extract_result.returncode == 0 and vtt_path.exists():
                    # extracted_languages 已经在前面添加了 clean_language
                    logger.info(f"Extracted subtitle: {lang_key} (codec: {codec_name}) from stream {stream_index}")
                else:
                    logger.warning(f"Failed to extract subtitle stream {stream_index}: {extract_result.stderr[:200] if extract_result.stderr else 'unknown error'}")

            except Exception as e:
                # 单个字幕流提取失败不影响其他字幕
                logger.warning(f"Error extracting subtitle stream {stream.get('index')}: {e}")
                continue

        return extracted_languages

    except Exception as e:
        logger.warning(f"Error extracting subtitles for video {video_id}: {e}")
        return []


def update_master_playlist_with_subtitles(video_id: str, languages: List[str]):
    """Update HLS master playlist to include subtitle tracks"""
    master_path = settings.PROCESSED_DIR / str(video_id) / "master.m3u8"
    if not master_path.exists():
        return

    with open(master_path, "r", encoding="utf-8") as f:
        content = f.read()

    lang_names = {
        "en": "English", "zh": "Chinese", "zh-Hans": "Chinese (Simplified)",
        "zh-Hant": "Chinese (Traditional)", "es": "Spanish", "fr": "French",
        "de": "German", "ja": "Japanese", "ko": "Korean", "pt": "Portuguese",
        "ru": "Russian", "ar": "Arabic", "it": "Italian", "hi": "Hindi"
    }

    subtitle_lines = []
    for lang in languages:
        lang_name = lang_names.get(lang, lang.upper())
        subtitle_lines.append(f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="sub_{lang}",NAME="{lang_name}",LANGUAGE="{lang}",URI="subtitles/{lang}.m3u8"')

    lines = content.split("\n")
    new_lines = []
    inserted = False

    for line in lines:
        if not inserted and line.startswith("#EXT-X-STREAM-INF"):
            new_lines.extend(subtitle_lines)
            new_lines.append("")
            inserted = True
        new_lines.append(line)

    with open(master_path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines))

    for lang in languages:
        subtitle_dir = settings.PROCESSED_DIR / str(video_id) / "subtitles"
        subtitle_playlist_path = subtitle_dir / f"{lang}.m3u8"

        # 查找匹配语言的 VTT 文件（可能名为 {lang}.vtt 或 track{index}_{lang}.vtt）
        vtt_files = list(subtitle_dir.glob(f"*{lang}*.vtt")) if subtitle_dir.exists() else []
        if vtt_files:
            subtitle_file_path = vtt_files[0]
            playlist_content = f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:10.0,\n{subtitle_file_path.name}\n#EXT-X-ENDLIST\n"
            with open(subtitle_playlist_path, "w", encoding="utf-8") as f:
                f.write(playlist_content)


@celery_app.task(bind=True)
def generate_subtitle_task(self, video_id: str, language: str = "en"):
    """使用 Whisper AI 自动生成视频字幕"""
    import whisper

    user_id = None

    logger.info(f"Starting subtitle generation for video {video_id}, language: {language}")

    with Session(engine) as session:
        video = session.get(Video, video_id)
        if not video:
            return {"status": "error", "message": "Video not found"}

        if not video.original_file_path:
            return {"status": "error", "message": "Original file not found"}

        user_id = str(video.user_id)
        task_id = self.request.id  # Celery task ID

        # 记录任务ID到数据库
        video.subtitle_task_id = task_id
        session.add(video)
        session.commit()

        input_path = settings.fs_path(video.original_file_path)
        # 如果转换后路径不存在，且原始路径是绝对路径，直接使用原始路径
        if not input_path.exists():
            if Path(video.original_file_path).is_absolute() and Path(video.original_file_path).exists():
                input_path = Path(video.original_file_path)
            else:
                video.subtitle_task_id = None
                session.add(video)
                session.commit()
                publish_subtitle_progress(video_id, user_id, 0, "failed", language)
                return {"status": "error", "message": "Original file does not exist"}

        try:
            # 推送开始进度
            publish_subtitle_progress(video_id, user_id, 5, "processing", language)

            model = whisper.load_model("base")
            publish_subtitle_progress(video_id, user_id, 10, "processing", language)

            # 转录（Whisper 不支持 progress_callback，使用线程方式）
            result = model.transcribe(
                str(input_path),
                language=language,
                verbose=False
            )

            publish_subtitle_progress(video_id, user_id, 75, "processing", language)

            subtitle_dir = settings.PROCESSED_DIR / str(video_id) / "subtitles"
            subtitle_dir.mkdir(parents=True, exist_ok=True)

            vtt_path = subtitle_dir / f"{language}.vtt"
            with open(vtt_path, "w", encoding="utf-8") as f:
                f.write("WEBVTT\n\n")
                for segment in result["segments"]:
                    start = format_timestamp(segment["start"])
                    end = format_timestamp(segment["end"])
                    text = segment["text"].strip()
                    f.write(f"{start} --> {end}\n{text}\n\n")

            publish_subtitle_progress(video_id, user_id, 90, "processing", language)

            languages = video.subtitle_languages or []
            if language not in languages:
                languages.append(language)
            video.subtitle_languages = languages
            video.auto_subtitle = True
            video.auto_subtitle_language = language
            session.add(video)
            session.commit()

            master_path = settings.PROCESSED_DIR / str(video_id) / "master.m3u8"
            if master_path.exists():
                update_master_playlist_with_subtitles(str(video_id), video.subtitle_languages)

            publish_subtitle_progress(video_id, user_id, 100, "completed", language)

            # 清除任务ID
            video.subtitle_task_id = None
            session.add(video)
            session.commit()

            logger.info(f"Subtitle generation completed for video {video_id}")
            return {"status": "completed", "language": language, "path": str(vtt_path)}

        except Exception as e:
            logger.error(f"Subtitle generation failed for video {video_id}: {e}")
            # 清除任务ID
            try:
                video.subtitle_task_id = None
                session.add(video)
                session.commit()
            except:
                pass
            if user_id:
                publish_subtitle_progress(video_id, user_id, 0, "failed", language)
            return {"status": "error", "message": str(e)}


# Celery Beat 定时任务配置
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    'compute-daily-trending': {
        'task': 'tasks.compute_daily_trending_task',
        'schedule': crontab(hour=2, minute=0),
        'options': {'queue': 'default'}
    },
    'compute-category-trending': {
        'task': 'tasks.compute_category_trending_task',
        'schedule': crontab(hour=3, minute=0),
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
    'zombie-ffmpeg-cleanup': {
        'task': 'tasks.cleanup_zombie_ffmpeg_task',
        'schedule': crontab(minute='*/10'),  # 每10分钟执行一次
        'options': {'queue': 'default'}
    },
}
