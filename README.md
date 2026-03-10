# MyVideo - AI-Enhanced Video On-Demand Platform

## 1. 项目概述 (Project Overview)
MyVideo 是一个基于 Python/FastAPI 构建的现代化视频点播平台，集成了高性能视频转码、流媒体播放、社交互动及个性化推荐功能。项目采用微服务架构思想设计，支持高并发视频处理与分发。

### 1.1 技术栈 (Tech Stack)
- **后端框架**: Python FastAPI (High-performance API)
- **数据库**: PostgreSQL (SQLModel ORM)
- **缓存与消息队列**: Redis
- **异步任务**: Celery (处理视频转码、后台作业)
- **多媒体处理**: FFmpeg (HLS 切片, 缩略图生成)
- **前端**: 原生 HTML5/CSS3 + ES6 JavaScript, Hls.js
- **部署**: Docker Compose, Nginx

---

## 2. 核心模块详解 (Module Breakdown)

### 2.1 视频核心模块 (Video Core)
负责视频全生命周期管理，从上传到分发。
- **视频上传**: 支持断点续传（基础支持），自动生成 UUID 文件名。
- **智能转码**: 后台异步任务队列处理，将源视频转码为 HLS (m3u8) 格式，支持多码率自适应（480p/720p）。
- **流媒体播放**: 集成 hls.js 播放器，支持自适应码率切换。
- **封面管理**: 支持自动截取视频帧作为封面，或用户上传自定义封面。

### 2.2 用户体验模块 (User Experience)
提升用户观看沉浸感与便捷性。
- **断点续播 (Resume Playback)**: 系统自动记录每位用户的观看进度，跨设备同步，再次打开视频时自动跳转至上次观看时间点（>5秒触发）。
- **观看历史 (History)**: 完整的用户观看足迹记录，按时间倒序排列。
- **完播率统计 (Smart Completion)**: 采用“有效完播”逻辑，当用户观看进度超过 90% 时自动计入完播数据，过滤片尾无效时长。

### 2.3 社交互动系统 (Social Interaction)
构建社区氛围，连接创作者与观众。
- **评论系统 (Comments)**: 支持视频下方发表评论，实时展示用户信息。
- **关注机制 (Follow System)**: 用户可以关注喜欢的创作者，创作者拥有粉丝管理能力。
- **点赞/踩 (Like/Dislike)**: 对视频内容进行态度表达。
- **通知中心 (Notification Center)**:
    - 聚合“关注”、“评论”、“点赞”消息。
    - 导航栏实时红点提醒（轮询机制）。
    - 支持一键已读和跳转直达。

### 2.4 用户与权限 (User & Auth)
- **身份认证**: 基于 JWT (JSON Web Tokens) 的无状态认证机制。
- **个人主页**: 展示用户头像、简介、粉丝/关注数及公开投稿视频。
- **创作者工作室**: 专属后台，管理上传的视频内容。

---

## 3. 部署与运维 (Deployment & Operations)

### 3.1 目录结构
```bash
/data/myvideo/
├── app/                # 后端核心代码 (FastAPI + Celery)
├── static/             # 前端静态资源 (HTML/JS/CSS)
├── nginx/              # Nginx 网关配置
├── postgres/           # 数据库数据卷
├── redis/              # Redis 数据卷
├── docker-compose.yml  # 容器编排文件
└── manage.sh           # 服务管理脚本
```

### 3.2 管理脚本 (manage.sh)
项目内置了快捷管理脚本，用于宿主机进程管理：
```bash
./manage.sh start   # 启动服务
./manage.sh stop    # 停止服务
./manage.sh restart # 重启服务
```

---

## 4. 版本历史 (Version History)
详细更新日志请查阅 [VERSION.md](./VERSION.md)。

- **v1.3**: 新增通知中心，完善社交闭环；修复个人主页数据统计。
- **v1.2**: 新增观看历史、断点续播、评论系统；优化完播率算法。
- **v1.1**: 基础视频上传、转码、播放功能；用户系统上线。
