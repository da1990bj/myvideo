# MyVideo 部署指南

## 目录

- [快速启动](#快速启动)
- [架构概述](#架构概述)
- [环境要求](#环境要求)
- [配置说明](#配置说明)
- [单机部署](#单机部署)
- [分布式 Celery 部署](#分布式-celery-部署)
- [Nginx 反向代理](#nginx-反向代理)
- [Docker 部署（可选）](#docker-部署可选)

---

## 快速启动

```bash
# 1. 克隆项目
git clone <repo-url> /data/myvideo
cd /data/myvideo

# 2. 创建虚拟环境
python3.10 -m venv venv
source venv/bin/activate

# 3. 安装依赖
pip install -r app/requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 填入实际配置

# 5. 启动应用
./manage.sh start

# 6. 启动 Celery Worker（另一个终端）
./manage_celery.sh start
```

---

## 架构概述

```
                           ┌─────────────┐
                           │   Redis     │
                           │  (Broker +  │
                           │   Pub/Sub)  │
                           └──────┬──────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
        ▼                         ▼                         ▼
┌───────────────┐      ┌───────────────┐      ┌───────────────┐
│  FastAPI 主站  │      │ Celery Worker │      │ Celery Worker │
│  (8000 端口)  │      │  (转码任务)   │      │  (转码任务)   │
└───────────────┘      └───────────────┘      └───────────────┘
        │                         │                         │
        ▼                         ▼                         ▼
   Nginx 反向代理            FFmpeg 转码                FFmpeg 转码
   (80/443 端口)                                          │
                                                          │
                                               可部署多台服务器
```

**组件说明：**

| 组件 | 端口 | 职责 |
|------|------|------|
| FastAPI (uvicorn) | 8000 | API 服务 |
| Celery Worker | - | 异步任务处理（转码、缩略图、推荐计算） |
| Redis | 6379 | Celery 消息队列 + WebSocket 进度推送 |
| Nginx | 80/443 | 反向代理 + 静态文件服务 |
| PostgreSQL | 5432 | 主数据库 |

---

## 环境要求

| 软件 | 版本 | 说明 |
|------|------|------|
| Python | 3.10+ | 推荐 anaconda 环境 |
| PostgreSQL | 12+ | 主数据库 |
| Redis | 6+ | 消息队列 |
| FFmpeg | 最新稳定版 | 视频转码 |
| Nginx | 任意稳定版 | 反向代理 |

---

## 配置说明

### .env 环境变量

```env
# 数据库
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_USER=myvideo
DATABASE_PASSWORD=your_password
DATABASE_NAME=myvideo_db

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0

# JWT 安全
SECRET_KEY=your-secret-key-change-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Celery（默认使用 Redis 作为 Broker）
CELERY_BROKER_URL=
CELERY_RESULT_BACKEND=

# 文件存储
MYVIDEO_ROOT=/data/myvideo
```

### config.py 配置

所有配置集中在 `app/config.py`，使用 Pydantic BaseSettings。

关键配置项：

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `MAX_UPLOAD_SIZE_MB` | 2048 | 上传文件大小限制（MB） |
| `REDIS_URL` | 自动计算 | Redis 连接 URL |
| `CELERY_BROKER` | 同 REDIS_URL | Celery 消息队列 |
| `CELERY_BACKEND` | 同 REDIS_URL | Celery 结果后端 |

---

## 单机部署

### 1. 启动 FastAPI 应用

```bash
./manage.sh start     # 启动
./manage.sh stop      # 停止
./manage.sh restart   # 重启
./manage.sh logs      # 查看日志
```

日志位置：`/data/myvideo/server.log`

### 2. 启动 Celery Worker

```bash
./manage_celery.sh start    # 启动 Worker
./manage_celery.sh stop     # 停止
./manage_celery.sh restart  # 重启
```

日志位置：`/data/myvideo/celery_worker.log`

### 3. 验证服务

```bash
# 检查 API
curl http://localhost:8000/

# 检查 Worker
ps aux | grep celery | grep -v grep
```

---

## 分布式 Celery 部署

### 架构说明

Celery 支持多台服务器部署 Worker，通过 Redis 作为消息队列协调。

```
主服务器 (192.168.1.10)          Worker 服务器 (192.168.1.20)
┌─────────────────┐              ┌─────────────────┐
│   FastAPI       │              │   Celery        │
│   (API 服务)    │              │   Worker        │
│                 │──────────────┼                 │
│   Celery        │     Redis    │   FFmpeg        │
│   Worker        │◄─────────────┤   (转码)        │
└─────────────────┘              └─────────────────┘
```

### Worker 服务器部署步骤

#### 1. 准备环境

```bash
# 在 Worker 服务器上执行
git clone <repo-url> /data/myvideo
cd /data/myvideo

# 创建虚拟环境
python3.10 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r app/requirements.txt
```

#### 2. 安装 FFmpeg

```bash
# Ubuntu/Debian
apt update && apt install ffmpeg

# 验证
ffmpeg -version
```

#### 3. 配置 .env

```env
# 指向主服务器的 Redis
REDIS_HOST=192.168.1.10
REDIS_PORT=6379

# 指向主服务器的数据库（Worker 只读）
DATABASE_HOST=192.168.1.10
DATABASE_PORT=5432
DATABASE_USER=myvideo
DATABASE_PASSWORD=xxx
DATABASE_NAME=myvideo_db

# JWT 与主服务器一致
SECRET_KEY=your-secret-key
```

#### 4. 启动 Worker

```bash
source venv/bin/activate
cd /data/myvideo

# 启动 Worker（只处理视频转码队列）
celery -A app.tasks.celery_app worker \
    --loglevel=info \
    --hostname=worker1@%h \
    -Q video,default
```

#### 5. 配置 systemd 服务

创建 `/etc/systemd/system/celery-worker.service`：

```ini
[Unit]
Description=MyVideo Celery Worker
After=network.target redis.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/data/myvideo
Environment=PATH=/data/myvideo/venv/bin:/usr/local/bin:/usr/bin
ExecStart=/data/myvideo/venv/bin/celery -A app.tasks.celery_app worker --loglevel=info --logfile=/data/myvideo/celery_worker.log -Q video,default
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable celery-worker
systemctl start celery-worker
```

### 多队列配置

任务默认使用 `default` 队列。视频转码可指定 `video` 队列：

```python
# app/routers/videos.py
transcode_video_task.delay(video_id, queue='video')
```

Worker 启动时指定监听的队列：

```bash
# 只处理视频转码
celery -A app.tasks.celery_app worker -Q video

# 处理所有任务
celery -A app.tasks.celery_app worker -Q default,video
```

### Celery Beat（定时任务）

推荐单独部署 Beat 服务：

```bash
celery -A app.tasks.celery_app beat --loglevel=info
```

定时任务定义在 `app/tasks.py`：

| 任务 | 执行时间 | 说明 |
|------|----------|------|
| `compute_all_recommendation_scores` | 每天 02:00 | 更新推荐分数 |
| `cold_storage_migration_task` | 每天 01:00 | 冷存储迁移 |

---

## Nginx 反向代理

参考配置：`/data/dev/nginx/conf.d/myvideo.conf`

```nginx
upstream fastapi_app {
    server host.docker.internal:8000;
    keepalive 64;
}

server {
    listen 80;
    server_name localhost;

    # 大文件上传（5GB）
    client_max_body_size 5000M;

    # 根路径
    location = / {
        root /usr/share/nginx/html;
        try_files /static/index.html =200;
    }

    # 静态文件
    location /static/ {
        alias /usr/share/nginx/html/static/;
        autoindex on;
        expires 7d;
        add_header Access-Control-Allow-Origin *;
    }

    # WebSocket
    location /socket.io {
        proxy_pass http://fastapi_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400s;
        proxy_buffering off;
    }

    # API
    location / {
        proxy_pass http://fastapi_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

---

## Docker 部署（可选）

项目支持 Docker Compose 部署（参考 `docker-compose.yml`）：

```bash
# 启动所有服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止
docker-compose down
```

Docker 相关文件：

| 文件 | 说明 |
|------|------|
| `docker-compose.yml` | 服务编排 |
| `Dockerfile` | 应用镜像 |
| `celery_config.py` | Celery 独立配置 |

---

## 常见问题

### 1. Worker 无法连接 Redis

```bash
# 检查 Redis 连接
redis-cli -h <REDIS_HOST> ping
```

### 2. 转码失败

```bash
# 检查 FFmpeg
ffmpeg -version

# 查看 Worker 日志
tail -f /data/myvideo/celery_worker.log
```

### 3. 上传失败 413

检查 Nginx 配置：
```nginx
client_max_body_size 5000M;
```

### 4. WebSocket 连接失败

检查 Nginx WebSocket 配置是否包含：
```nginx
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
```

---

## 监控

### 检查 Worker 状态

```bash
celery -A app.tasks.celery_app inspect active
celery -A app.tasks.celery_app inspect stats
```

### 查看队列长度

```bash
redis-cli LLEN celery
```
