from celery import Celery
import os
import subprocess
import re
import math
import json
import asyncio
import logging
import shutil
from pathlib import Path
from sqlmodel import Session, select
from database import engine
from data_models import Video
from config import settings

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
)

def run_ffmpeg(cmd, video, session, start_percent, end_percent, total_duration, push_progress_callback=None):
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
    """
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

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

                        # 触发进度推送回调（用于WebSocket）
                        if push_progress_callback:
                            try:
                                push_progress_callback(str(video.id), global_percent)
                            except Exception as e:
                                logger.warning(f"Error in progress callback: {e}")

    process.wait()
    if process.returncode != 0:
        raise Exception("FFmpeg error")

@celery_app.task(bind=True)
def transcode_video_task(self, video_id: str):
    with Session(engine) as session:
        video = session.exec(select(Video).where(Video.id == video_id)).first()
        if not video: return "Video not found"

        try:
            video.status = "processing"
            video.progress = 0
            session.add(video)
            session.commit()

            # 广播转码队列更新给管理员
            try:
                from socketio_handler import manager
                from main import sio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    manager.broadcast_transcode_update(sio, "transcode_queue_changed", {
                        "video_id": str(video.id),
                        "status": "processing",
                        "title": video.title,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                )
                loop.close()
            except Exception as e:
                logger.warning(f"Failed to broadcast queue update: {e}")

            # 导入WebSocket管理器用于推送进度
            try:
                from socketio_handler import manager
                from main import sio
                websocket_available = True
            except (ImportError, RuntimeError) as e:
                logger.warning(f"WebSocket not available: {e}")
                websocket_available = False

            # 创建进度推送回调函数
            def push_progress_callback(video_id_str, progress):
                """
                该回调函数在FFmpeg进度更新时被调用
                通过WebSocket推送进度给创作者
                """
                if websocket_available:
                    try:
                        # 使用asyncio在Celery worker中运行异步函数
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(
                            manager.push_progress(
                                sio=sio,
                                user_id=str(video.user_id),
                                video_id=video_id_str,
                                progress=progress,
                                status="processing"
                            )
                        )
                        loop.close()
                    except Exception as e:
                        logger.warning(f"Failed to push progress via WebSocket: {e}")

            input_path = video.original_file_path
            
            # HLS 输出目录: /static/videos/processed/{video_id}/
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
            # 解析 ffprobe 输出
            lines = probe.stdout.strip().split('\n')
            total_duration = 0.0
            width = 0
            height = 0

            # ffprobe output format depends on stream order, try to parse
            for line in lines:
                if line.replace('.', '', 1).isdigit(): # Simple check for number
                    val = float(line)
                    if val > 10000: # Assuming duration won't be that huge or width/height are ints
                         # This parsing is brittle. Better use json output.
                         pass

            # Robust parsing using JSON
            probe_json = subprocess.run(
                 ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-show_entries", "stream=width,height", "-of", "json", input_path],
                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            import json
            data = json.loads(probe_json.stdout)
            total_duration = float(data['format']['duration'])

            video_stream = next((s for s in data.get('streams', []) if 'width' in s), None)
            if video_stream:
                width = int(video_stream['width'])
                height = int(video_stream['height'])

            video.duration = int(total_duration)
            session.add(video)
            session.commit()

            # 2. 生成封面 (截取第 5 秒，避开黑屏)
            subprocess.run([
                "ffmpeg", "-i", input_path, "-ss", "00:00:05", "-vframes", "1", thumb_path, "-y"
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # 3. 自适应转码策略
            # 定义支持的分辨率阶梯: (height, bitrate, audio_bitrate, name)
            resolutions = [
                (1440, "6000k", "192k", "2k"),
                (1080, "4500k", "192k", "1080p"),
                (720, "2500k", "128k", "720p"),
                (480, "1000k", "96k", "480p")
            ]

            target_resolutions = []
            # 总是包含一个不高于原画质的最低档，或者原画质对应的档位
            # 简单的逻辑：只要原视频高度 >= 目标高度 * 0.9，就生成该档位
            # 且至少保留一个最低档(480p)如果原视频很小

            found_higher = False
            for r_h, r_b, r_a, r_n in resolutions:
                if height >= r_h * 0.9:
                    target_resolutions.append((r_h, r_b, r_a, r_n))
                    found_higher = True

            # 如果原视频小于 480p，则只转码一个原分辨率的 (或者就强制 480p 但这会放大)
            # 为了简单，如果没匹配到任何（即小于480p），则添加 480p（ffmpeg会自动处理upscale或者我们保持原样）
            # 或者我们添加一个 "original" 档位?
            # 现在的策略：如果空，则强制 480p
            if not target_resolutions:
                target_resolutions.append((480, "1000k", "96k", "480p"))

            # 计算进度步长
            step = 95 / len(target_resolutions)
            current_progress_base = 0

            master_playlist = ["#EXTM3U", "#EXT-X-VERSION:3"]

            for r_h, r_b, r_a, r_n in target_resolutions:
                cmd = [
                    "ffmpeg", "-i", input_path,
                    "-c:v", "libx264", "-b:v", r_b, "-maxrate", r_b, "-bufsize", str(int(r_b[:-1])*2)+"k",
                    "-vf", f"scale=-2:{r_h}", "-c:a", "aac", "-b:a", r_a,
                    "-hls_time", "6", "-hls_playlist_type", "vod",
                    "-hls_segment_filename", os.path.join(hls_dir, f"{r_n}_%03d.ts"),
                    "-progress", "pipe:1",
                    os.path.join(hls_dir, f"{r_n}.m3u8"), "-y"
                ]

                end_p = current_progress_base + step
                run_ffmpeg(cmd, video, session, current_progress_base, end_p, total_duration, push_progress_callback)
                current_progress_base = end_p

                # Add to master
                # Need bandwidth estimation. rough calc.
                bw = int(r_b[:-1]) * 1000 + int(r_a[:-1]) * 1000
                master_playlist.append(f'#EXT-X-STREAM-INF:BANDWIDTH={bw},RESOLUTION={int(r_h * width / height)}x{r_h},NAME="{r_n}"')
                master_playlist.append(f"{r_n}.m3u8")

            # 5. 生成 Master Playlist
            master_path = os.path.join(hls_dir, "master.m3u8")
            with open(master_path, "w") as f:
                f.write('\n'.join(master_playlist))

            # 完成 (存储 master.m3u8 的路径)
            video.processed_file_path = f"/static/videos/processed/{video.id}/master.m3u8"
            video.thumbnail_path = f"/static/thumbnails/{video.id}.jpg"
            video.status = "completed"
            video.progress = 100

            # 广播转码队列更新给管理员
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    manager.broadcast_transcode_update(sio, "transcode_queue_changed", {
                        "video_id": str(video.id),
                        "status": "completed",
                        "title": video.title,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                )
                loop.close()
            except Exception as e:
                logger.warning(f"Failed to broadcast queue update: {e}")

            # 推送完成状态
            if websocket_available:
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(
                        manager.push_progress(
                            sio=sio,
                            user_id=str(video.user_id),
                            video_id=video_id,
                            progress=100,
                            status="completed"
                        )
                    )
                    loop.close()
                except Exception as e:
                    logger.warning(f"Failed to push completion status: {e}")

        except Exception as e:
            video.status = "failed"
            logger.error(f"Transcoding error for video {video_id}: {e}")

            # 广播转码队列更新给管理员
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    manager.broadcast_transcode_update(sio, "transcode_queue_changed", {
                        "video_id": str(video.id),
                        "status": "failed",
                        "title": video.title,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                )
                loop.close()
            except Exception as broadcast_err:
                logger.warning(f"Failed to broadcast queue update: {broadcast_err}")

            # 推送失败状态
            if websocket_available:
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(
                        manager.push_progress(
                            sio=sio,
                            user_id=str(video.user_id),
                            video_id=video_id,
                            progress=0,
                            status="failed"
                        )
                    )
                    loop.close()
                except Exception as push_error:
                    logger.warning(f"Failed to push error status: {push_error}")

            return f"Error: {e}"

        finally:
            session.add(video)
            session.commit()

    return "Success"

import random
from datetime import datetime, timedelta

@celery_app.task(bind=True)
def regenerate_thumbnail_task(self, video_id: str):
    """重新生成封面 (随机帧)"""
    with Session(engine) as session:
        video = session.get(Video, video_id)
        if not video: return "Video not found"
        
        try:
            # 1. 确保获取准确时长
            input_path = video.original_file_path
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            try:
                total_duration = float(probe.stdout.strip())
            except:
                total_duration = 10.0 # 默认 10s
            
            # 2. 随机时间点 (避开首尾)
            random_time = random.uniform(1, max(1, total_duration - 1))
            
            # 3. 格式化时间戳
            m, s = divmod(int(random_time), 60)
            h, m = divmod(m, 60)
            timestamp = f"{h:02d}:{m:02d}:{s:02d}"
            
            # 4. 生成新文件名 (避免缓存)
            import time
            new_filename = f"{video.id}_{int(time.time())}.jpg"
            thumb_rel_path = f"/static/thumbnails/{new_filename}"
            thumb_abs_path = str(settings.THUMBNAILS_DIR / new_filename)

            # 删除旧封面 (可选)
            old_thumb_path = settings.fs_path(video.thumbnail_path) if video.thumbnail_path else None
            if old_thumb_path and old_thumb_path.exists():
                try: old_thumb_path.unlink()
                except: pass

            # 截图
            subprocess.run([
                "ffmpeg", "-ss", timestamp, "-i", input_path, "-vframes", "1", thumb_abs_path, "-y"
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # 更新数据库
            video.thumbnail_path = thumb_rel_path
            session.add(video)
            session.commit()
            
            return f"Thumbnail regenerated at {timestamp}"
        except Exception as e:
            print(f"Error: {e}")
            return f"Error: {e}"


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
            # 获取所有活跃用户
            active_users = session.exec(select(User).where(User.is_active == True)).all()
            logger.info(f"Found {len(active_users)} active users")

            processed_count = 0
            error_count = 0

            for user in active_users:
                try:
                    # 异步运行推荐计算
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

    Args:
        old_dirs: {"uploads": "/old/path", "processed": "/old/path", "thumbnails": "/old/path"}
        new_dirs: {"uploads": "/new/path", "processed": "/new/path", "thumbnails": "/new/path"}
    """
    from time import sleep

    logger.info(f"Starting storage migration: {old_dirs} -> {new_dirs}")

    # 获取迁移间隔配置
    migration_delay = getattr(settings, 'STORAGE_MIGRATION_DELAY', 0.5)
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
                                # 移动整个目录
                                for item in old_dir.iterdir():
                                    shutil.move(str(item), str(new_dir / item.name))
                                # 删除旧目录
                                try:
                                    old_dir.rmdir()
                                except OSError:
                                    pass  # 目录非空，忽略
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

                    # 控制迁移速度
                    if migration_delay > 0:
                        sleep(migration_delay)

                    # 更新任务进度
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
def cold_storage_migration_task(self):
    """
    定时检查并迁移冷存储视频

    条件：created_at 超过 COLD_STORAGE_TRIGGER_DAYS 天 AND views < COLD_STORAGE_TRIGGER_VIEWS
    """
    if not settings.COLD_STORAGE_ENABLED:
        logger.info("Cold storage is disabled, skipping migration check")
        return {"status": "skipped", "reason": "cold_storage_disabled"}

    logger.info("Starting cold storage migration check...")

    try:
        with Session(engine) as session:
            # 计算时间阈值
            days_threshold = settings.COLD_STORAGE_TRIGGER_DAYS
            views_threshold = settings.COLD_STORAGE_TRIGGER_VIEWS
            cutoff_date = datetime.utcnow() - timedelta(days=days_threshold)

            # 查询符合条件的视频：非冷存储、超期、低播放量
            candidates = session.exec(
                select(Video).where(
                    Video.is_cold == False,
                    Video.created_at < cutoff_date,
                    Video.views < views_threshold,
                    Video.status == "completed"  # 只迁移已完成视频
                )
            ).all()

            migrated_count = 0
            failed_count = 0

            for video in candidates:
                try:
                    # 获取原始文件路径
                    original_path = settings.fs_path(video.original_file_path)
                    processed_path = settings.fs_path(video.processed_file_path) if video.processed_file_path else None

                    # 复制到冷存储
                    if original_path.exists():
                        cold_upload_dir = settings.COLD_STORAGE_UPLOADS_DIR
                        cold_upload_dir.mkdir(parents=True, exist_ok=True)

                        import shutil
                        cold_file_path = cold_upload_dir / original_path.name
                        shutil.copy2(original_path, cold_file_path)

                        # 删除主存储文件（移走后删除）
                        original_path.unlink()
                        logger.info(f"Migrated original file for video {video.id} to {cold_file_path}")

                    # 处理转码文件
                    if processed_path and processed_path.exists():
                        cold_processed_dir = settings.COLD_STORAGE_PROCESSED_DIR
                        cold_processed_dir.mkdir(parents=True, exist_ok=True)

                        import shutil
                        cold_processed_file = cold_processed_dir / processed_path.name
                        shutil.copy2(processed_path, cold_processed_file)
                        processed_path.unlink()
                        logger.info(f"Migrated processed file for video {video.id} to {cold_processed_file}")

                    # 更新数据库
                    video.is_cold = True
                    video.cold_stored_at = datetime.utcnow()
                    session.add(video)
                    session.commit()

                    migrated_count += 1
                    logger.info(f"Video {video.id} migrated to cold storage")

                except Exception as e:
                    failed_count += 1
                    logger.error(f"Failed to migrate video {video.id}: {e}")
                    # 回滚事务
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


# Celery Beat 定时任务配置
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    'compute-recommendations-daily': {
        'task': 'tasks.compute_all_recommendation_scores',
        'schedule': crontab(hour=2, minute=0),  # 每天凌晨2点执行
        'options': {'queue': 'default'}
    },
    'cold-storage-migration-daily': {
        'task': 'tasks.cold_storage_migration_task',
        'schedule': crontab(hour=1, minute=0),  # 每天凌晨1点执行冷存储检查
        'options': {'queue': 'default'}
    },
}
