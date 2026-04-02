# MyVideo 应用镜像
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖 (FFmpeg 用于视频转码)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制 requirements.txt 并安装 Python 依赖
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app /app/app
COPY celery_config.py /app/celery_config.py

# 创建必要的目录
RUN mkdir -p /app/static/videos/uploads \
             /app/static/videos/processed \
             /app/static/thumbnails \
             /app/static/avatars \
             /app/data \
             /app/cold_storage

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 默认命令: 启动 FastAPI 应用
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
