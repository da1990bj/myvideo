# MyVideo 架构图

## 系统架构

```
                                    ┌─────────────────────────────────────┐
                                    │              用户浏览器               │
                                    │  (HTML5 + hls.js + WebSocket)      │
                                    └───────────────┬─────────────────────┘
                                                    │
                                        HTTP/HTTPS │ WebSocket
                                                    │
                                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                              Nginx 反向代理                                │
│                                                                          │
│   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────────────┐   │
│   │ 静态文件  │   │   API    │   │ WebSocket│   │   HLS 视频流     │   │
│   │ /static/ │   │    /     │   │ /socket.io│  │ /static/videos/* │   │
│   └──────────┘   └────┬─────┘   └────┬─────┘   └──────────────────┘   │
│                       │               │                                  │
│                       └───────┬───────┘                                  │
└───────────────────────────────┼──────────────────────────────────────────┘
                                │
                ┌───────────────┼───────────────┐
                │               │               │
                ▼               ▼               ▼
        ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
        │  FastAPI    │ │ Celery      │ │   Redis     │
        │  (8000)    │ │ Worker      │ │  (6379)     │
        │             │ │             │ │             │
        │  API 服务    │ │ 异步任务处理  │ │ Broker      │
        │  WebSocket  │ │  - 转码     │ │ Pub/Sub     │
        │  处理器     │ │  - 缩略图   │ │ Cache       │
        └──────┬──────┘ └──────┬──────┘ └─────────────┘
               │                │
               │                │ FFmpeg
               │                ▼
               │         ┌─────────────┐
               │         │  视频转码    │
               │         │  (HLS)     │
               │         └──────┬──────┘
               │                │
               │                ▼
               │         ┌─────────────┐
               │         │  转码输出    │
               │         │ (m3u8/ts)  │
               │         └─────────────┘
               │
               ▼
        ┌─────────────┐
        │ PostgreSQL   │
        │  (5432)     │
        │             │
        │ 用户/视频/   │
        │ 社交数据     │
        └─────────────┘
```

## 模块交互

### 1. 视频上传流程

```
用户 ──[上传]──▶ FastAPI ──[写入]──▶ 磁盘存储
                              │
                              └──[触发任务]──▶ Celery Worker
                                                    │
                                                    ▼
                                            FFmpeg 转码 (HLS)
                                                    │
                                                    ▼
                                          Redis Pub/Sub 推送进度
                                                    │
                                                    ▼
                                          WebSocket ──[实时进度]──▶ 用户浏览器
```

### 2. 视频播放流程

```
用户浏览器 ──[请求 m3u8]──▶ Nginx ──[静态文件]──▶ 已转码视频
              │
              └──[HLS.js 解析]──▶ 自动选择最佳码率
```

### 3. 推荐系统流程

```
定时任务 ──[每天 02:00]──▶ Celery Beat ──[计算]──▶ 推荐分数
                                                      │
                                                      ▼
                                               Redis 缓存
                                                      │
                                                      ▼
                                    GET /recommendations ──▶ 推荐列表
```

## 数据库模型

```
┌─────────────────┐     ┌─────────────────┐
│      User       │     │      Role       │
├─────────────────┤     ├─────────────────┤
│ id (UUID)       │◄───┐│ id (PK)         │
│ username        │    ││ name            │
│ email           │    ││ permissions     │
│ password_hash   │    └─────────────────┘
│ is_admin        │          │
│ avatar_path     │          │
│ bio             │          │
└────────┬────────┘          │
         │                   │
         │      ┌────────────┘
         │      │
         ▼      ▼
┌─────────────────────┐
│     UserRole        │
├─────────────────────┤
│ user_id (FK)  ◄────┘
│ role_id (FK)
└─────────────────────┘

┌─────────────────┐     ┌─────────────────┐
│     Video       │     │    Category     │
├─────────────────┤     ├─────────────────┤
│ id (UUID)       │◄──┐│ id (PK)         │
│ user_id (FK)   │   ││ name            │
│ title           │   ││ slug            │
│ description     │   └─────────────────┘
│ status          │
│ visibility      │          ┌─────────────────┐
│ views           │          │   VideoLike     │
│ duration        │          ├─────────────────┤
│ thumbnail_path  │          │ user_id (FK)    │
│ processed_path  │          │ video_id (FK)  │
└────────┬────────┘          └─────────────────┘
         │
         │ 1:N
         ▼
┌─────────────────────┐
│    VideoComment      │
├─────────────────────┤
│ id (UUID)           │
│ video_id (FK)       │
│ user_id (FK)        │
│ content             │
│ parent_id (FK)      │ (自关联，嵌套评论)
│ is_deleted          │
└─────────────────────┘

┌─────────────────────┐
│   UserFollow        │
├─────────────────────┤
│ follower_id (FK)     │ ◄── 关注者
│ followed_id (FK)     │ ◄── 被关注者
└─────────────────────┘

┌─────────────────────┐
│    UserBlock        │
├─────────────────────┤
│ blocker_id (FK)     │ ◄── 拉黑者
│ blocked_id (FK)     │ ◄── 被拉黑者
└─────────────────────┘
```

## 前端路由

| 页面 | 路径 | 说明 |
|------|------|------|
| 首页 | `/static/index.html` | 视频列表、推荐 |
| 视频播放 | `/static/video.html?id=xxx` | 播放器、评论 |
| 个人主页 | `/static/profile.html?id=xxx` | 用户信息、视频 |
| 创作者工作室 | `/static/dashboard.html` | 上传、管理 |
| 上传视频 | `/static/upload.html` | 上传表单 |
| 账号设置 | `/static/settings.html` | 个人信息 |
| 观看历史 | `/static/history.html` | 历史记录 |
| 通知中心 | `/static/notifications.html` | 消息通知 |
| 登录 | `/static/login.html` | 登录页 |
| 注册 | `/static/register.html` | 注册页 |
| 管理后台 | `/static/admin/index.html` | 管理员面板 |

## 后台任务 (Celery)

| 任务 | 触发方式 | 功能 |
|------|----------|------|
| `transcode_video_task` | 上传时 | FFmpeg 转码为 HLS |
| `regenerate_thumbnail_task` | 手动 | 重新生成封面 |
| `compute_all_recommendation_scores` | 每天 02:00 | 更新推荐分数 |
| `migrate_storage_task` | 手动 | 迁移视频文件 |
| `cold_storage_migration_task` | 每天 01:00 | 冷存储迁移 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_HOST` | localhost | PostgreSQL 主机 |
| `DATABASE_PORT` | 5432 | PostgreSQL 端口 |
| `DATABASE_USER` | myvideo | 数据库用户 |
| `DATABASE_PASSWORD` | - | 数据库密码 |
| `DATABASE_NAME` | myvideo_db | 数据库名 |
| `REDIS_HOST` | localhost | Redis 主机 |
| `REDIS_PORT` | 6379 | Redis 端口 |
| `REDIS_PASSWORD` | - | Redis 密码 |
| `SECRET_KEY` | - | JWT 密钥 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 1440 | Token 过期时间 |
