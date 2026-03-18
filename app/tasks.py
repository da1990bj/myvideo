from celery import Celery
import os
import subprocess
import re
import math
import json
import asyncio
import logging
from sqlmodel import Session, select
from database import engine
from data_models import Video

logger = logging.getLogger(__name__)

# 配置 Celery
celery_app = Celery(
    "video_tasks",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0"
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
            thumb_dir = os.path.dirname(input_path).replace("videos/uploads", "thumbnails")
            if "static" not in thumb_dir: thumb_dir = "/data/myvideo/static/thumbnails"
            os.makedirs(thumb_dir, exist_ok=True)
            thumb_path = os.path.join(thumb_dir, f"{video.id}.jpg")

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
            thumb_abs_path = f"/data/myvideo/static/thumbnails/{new_filename}"
            
            # 删除旧封面 (可选)
            if video.thumbnail_path and os.path.exists(video.thumbnail_path.replace("/static", "/data/myvideo/static")):
                try: os.remove(video.thumbnail_path.replace("/static", "/data/myvideo/static"))
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


# Celery Beat 定时任务配置
celery_app.conf.beat_schedule = {
    'compute-recommendations-daily': {
        'task': 'tasks.compute_all_recommendation_scores',
        'schedule': 3600 * 24,  # 每24小时执行一次（可使用crontab更精确控制）
        'options': {'queue': 'default'}
    },
}

# 如果需要更精确的时间控制，可以使用 crontab：
# from celery.schedules import crontab
# celery_app.conf.beat_schedule = {
#     'compute-recommendations-daily': {
#         'task': 'tasks.compute_all_recommendation_scores',
#         'schedule': crontab(hour=2, minute=0),  # 每天凌晨2点执行
#     },
# }
