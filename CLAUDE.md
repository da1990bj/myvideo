# CLAUDE.md

此文件为 Claude Code (claude.ai/code) 在处理本仓库代码时提供指南。

## 常用命令 (Commands)

- **启动 API 服务器**: `./manage.sh start` (在 8000 端口运行 Uvicorn，开启热重载)
- **停止 API 服务器**: `./manage.sh stop`
- **重启 API 服务器**: `./manage.sh restart`
- **启动 Celery Worker**: `./manage_celery.sh start` (处理异步任务，如视频转码)
- **停止 Celery Worker**: `./manage_celery.sh stop`
- **启动基础设施**: `docker-compose up -d` (启动 Postgres, Redis, Nginx)
- **停止基础设施**: `docker-compose down`
- **查看服务器日志**: `tail -f server.log`
- **查看 Worker 日志**: `tail -f celery_worker.log`
- **安装依赖**: `pip install -r app/requirements.txt`
- **运行手动测试**: `python app/test_api.py <TOKEN>` (需要有效的 JWT 令牌)
- **数据库迁移**: `python migrate_comments.py` (升级评论系统表结构)

## 架构 (Architecture)

- **后端框架**: Python FastAPI (`app/main.py`)
- **数据库**: PostgreSQL + SQLModel ORM (`app/data_models.py`)
- **异步任务**: Celery + Redis 消息代理 (`app/tasks.py`)
- **前端**: 原生 HTML5/CSS3/ES6 JavaScript (`static/`)，由 Nginx 提供服务
- **视频流**: HLS (m3u8) 格式，通过 Celery 任务调用 FFmpeg 进行转码
- **认证**: JWT (JSON Web Tokens) 无状态认证
- **部署**:
    - **Docker**: 管理 PostgreSQL, Redis 和 Nginx 容器 (`docker-compose.yml`)
    - **本地进程**: FastAPI 应用和 Celery worker 直接在宿主机运行，通过 Shell 脚本管理 (`manage.sh`, `manage_celery.sh`)

## 目录结构 (Directory Structure)

- `app/`: 后端核心代码 (FastAPI + Celery)
- `static/`: 前端静态资源 (HTML, JS, CSS, 上传的视频)
- `nginx/`: Nginx 配置文件
- `postgres/` & `redis/`: 数据卷和配置
- `manage.sh` & `manage_celery.sh`: 服务管理脚本
- `migrate_comments.py`: 评论系统升级迁移脚本

## 核心功能与实现 (Key Features & Implementations)

- **视频处理**: 视频上传后，由 Celery 异步处理生成 HLS 流和缩略图。
- **播放**: 使用 `hls.js` 进行自适应码率流媒体播放。
- **用户系统**: 包含认证、个人资料（支持用户名访问）、观看历史和社交功能。
- **评论系统**: 支持嵌套回复（二级扁平化）、软删除（用户/管理员）、以及@提及功能。
- **通知**: 基于轮询的用户交互通知系统（包含评论回复通知）。
- **标签系统**: 独立的标签表结构，支持多对多关联，允许技术类标签（如 .+#）。
