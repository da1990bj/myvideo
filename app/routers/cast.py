"""
DLNA 投屏路由
使用 upnp-client 发现和控制局域网内的 DLNA 设备
"""
import socket
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlmodel import Session

from database import get_session
from data_models import Video

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cast", tags=["投屏"])


def get_server_url() -> str:
    """获取服务器URL，用于投屏的视频地址"""
    # 获取本机IP
    hostname = socket.gethostname()
    # 优先使用真实IP（不是127.0.0.1）
    try:
        server_ip = socket.gethostbyname(hostname)
    except Exception:
        server_ip = "127.0.0.1"

    return f"http://{server_ip}:8000"


def discover_dlna_devices(timeout: float = 5.0):
    """
    发现局域网内的 DLNA 设备

    Args:
        timeout: 发现超时时间（秒）

    Returns:
        设备列表
    """
    try:
        import upnpclient
        devices = upnpclient.discover(timeout=timeout)
        return devices
    except ImportError:
        logger.warning("upnpclient library not installed")
        return []
    except Exception as e:
        logger.error(f"DLNA device discovery failed: {e}")
        return []


@router.get("/devices")
async def discover_devices():
    """
    发现局域网内的 DLNA 投屏设备

    返回设备列表，包含设备名称和ID
    """
    devices = discover_dlna_devices()

    result = []
    for device in devices:
        # 尝试识别设备类型
        device_type = "unknown"
        if hasattr(device, 'device_type') and device.device_type:
            dt = str(device.device_type).lower()
            if "renderer" in dt or "mediarenderer" in dt:
                device_type = "renderer"
            elif "server" in dt or "mediaserver" in dt:
                device_type = "server"

        result.append({
            "id": device.usn,  # 使用 USN 作为唯一ID
            "name": getattr(device, 'name', str(device)),
            "type": device_type,
            "manufacturer": getattr(device, 'manufacturer', '')
        })

    return {"devices": result}


@router.post("/play")
async def play_video(
    device_id: str = Query(..., description="设备ID"),
    video_id: str = Query(..., description="视频ID"),
    session: Session = Depends(get_session)
):
    """
    投屏播放指定视频

    Args:
        device_id: 目标设备ID（来自 /devices 列表）
        video_id: 要播放的视频ID
    """
    # 获取视频信息
    video = session.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if not video.original_file_path:
        raise HTTPException(status_code=400, detail="Video original file not found")

    # 使用原始视频文件投屏（HLS格式DLNA设备不支持）
    # 原始文件是 mp4/mkv 格式，DLNA 设备可直接播放
    server_url = get_server_url()
    video_url = f"{server_url}{video.original_file_path}"

    logger.info(f"Casting video {video_id} to device {device_id}, URL: {video_url}")

    # 查找目标设备
    devices = discover_dlna_devices()
    target_device = None
    for device in devices:
        if device.usn == device_id:
            target_device = device
            break

    if not target_device:
        raise HTTPException(status_code=404, detail="Device not found")

    try:
        # 调用设备的播放方法
        # upnpclient 提供的播放接口
        if hasattr(target_device, 'play'):
            # 设置媒体URL
            if hasattr(target_device, 'set_current_uri'):
                target_device.set_current_uri(video_url)
            elif hasattr(target_device, 'av_transport'):
                target_device.av_transport.SetAVTransportURI(
                    InstanceID=0,
                    CurrentURI=video_url,
                    CurrentURIMetadata=''
                )

            # 开始播放
            target_device.play()
            logger.info(f"Cast started successfully to {target_device.name}")
            return {
                "status": "playing",
                "device": target_device.name,
                "video_url": video_url
            }
        else:
            raise HTTPException(status_code=400, detail="Device does not support playback")

    except Exception as e:
        logger.error(f"Cast failed: {e}")
        raise HTTPException(status_code=500, detail=f"Cast failed: {str(e)}")


@router.post("/stop")
async def stop_playback(device_id: str = Query(...)):
    """
    停止投屏

    Args:
        device_id: 目标设备ID
    """
    devices = discover_dlna_devices()
    target_device = None

    for device in devices:
        if device.usn == device_id:
            target_device = device
            break

    if not target_device:
        raise HTTPException(status_code=404, detail="Device not found")

    try:
        if hasattr(target_device, 'stop'):
            target_device.stop()
            return {"status": "stopped", "device": target_device.name}
        else:
            raise HTTPException(status_code=400, detail="Device does not support stop")
    except Exception as e:
        logger.error(f"Stop cast failed: {e}")
        raise HTTPException(status_code=500, detail=f"Stop failed: {str(e)}")
