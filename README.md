# MyVideo v1.0.4 - 视频点播平台

## 项目简介

MyVideo 是一个功能完整的视频点播平台，支持视频上传、转码、播放、用户社交互动、内容审核、推荐系统和管理后台。采用 Python/FastAPI 构建后端，原生 HTML5/JS 实现前端，支持 HLS 自适应码率流媒体播放。

**当前版本**: v1.0.4 | **Python**: 3.10+ | **状态**: 生产可用

---

## 使用框架

| 组件 | 技术 | 版本 |
|------|------|------|
| 后端框架 | FastAPI | 0.100+ |
| ORM | SQLModel | 0.0.13+ |
| 数据库 | PostgreSQL | 12+ |
| 缓存/消息队列 | Redis | 6+ |
| 异步任务 | Celery | 5.3+ |
| 视频处理 | FFmpeg | 最新稳定版 |
| 前端 | HTML5/CSS3/JS + hls.js | - |
| WebSocket | python-socketio | 5.9+ |
| 认证 | JWT (python-jose) | - |
| 字幕生成 | OpenAI Whisper | - |

### 核心依赖

```
fastapi>=0.100
uvicorn[standard]
sqlalchemy
sqlmodel
pydantic
psycopg2-binary
celery
redis
python-multipart
python-jose[cryptography]
passlib[bcrypt]
ffmpeg-python
python-dotenv
python-socketio>=5.9
python-engineio>=4.7
aioredis>=2.0
upnpclient>=1.0.0
openai-whisper
```

---

## 功能设计

### 1. 视频模块

| 功能 | 说明 |
|------|------|
| 视频上传 | 支持 MKV/MP4/MOV/AVI/WMV/WebM 等格式，分片上传支持 >2GB 文件 |
| HLS 转码 | FFmpeg 转为多码率自适应流 (1440p/1080p/720p/480p) |
| 实时进度 | WebSocket + Redis Pub/Sub 推送转码进度 |
| 封面提取 | 自动截取第 5 秒帧作为封面 |
| 字幕功能 | 支持多语言字幕，可上传 .vtt/.srt 文件或自动生成 (Whisper AI) |
| 播放位置记忆 | localStorage 自动保存/恢复播放进度 |
| 断点续播 | 观看历史记录支持续播 |
| 完播率统计 | 记录完整观看次数 |
| 视频分享 | 生成分享链接，支持过期时间设置 |
| 分片上传 | 5MB 分片大小，支持断点续传 |

### 2. 转码队列系统

| 功能 | 说明 |
|------|------|
| 优先级队列 | default / vip / priority 三级队列 |
| Aging 机制 | 普通用户优先级随等待时间递增 (0.5/小时) |
| VIP 加速 | VIP 用户基础优先级 10，aging 较慢 |
| 付费插队 | 消耗积分 (默认 5 积分) 加速优先级 |
| 暂停/恢复/取消 | 支持中途暂停、恢复和取消任务 |
| 节点显示 | 显示任务执行的 Worker 节点名称 |
| 排队信息 | 显示排队位置和预估等待时间 |
| 重试机制 | 失败任务支持重试 (用户限1次，管理员不限) |

### 3. 播放器功能

- 自定义 HTML5 播放器控件
- HLS 自适应码率流
- **键盘快捷键**: 空格键播放/暂停，左右方向键 ±5 秒
- 画中画模式 (Picture-in-Picture)
- 全屏/剧院模式
- 音量控制 + 播放速度调节
- DLNA/投屏功能 (upnpclient)
- 字幕选择 (无字幕时按钮灰色禁用)
- 播放位置记忆 (刷新后继续播放)
- 推荐视频追踪

### 4. 用户与社交

| 功能 | 说明 |
|------|------|
| JWT 认证 | 注册、登录、修改密码、私密视频密码 |
| 个人主页 | 粉丝、关注、投稿统计 |
| 关注/粉丝 | 用户关注系统 |
| 拉黑功能 | 用户黑名单管理 |
| 点赞/点踩 | 视频和评论的点赞点踩 |
| 收藏 | 视频和合集的收藏 |
| 评论系统 | 嵌套回复、点赞/点踩、置顶 |
| 通知中心 | 关注、评论、点赞等通知 |
| VIP 用户 | 专有加速通道 |

### 5. 内容管理

| 功能 | 说明 |
|------|------|
| 合集/播放列表 | 视频合集管理，支持排序 |
| 分类与标签 | 视频分类和标签系统 |
| 视频审核 | 上架/下架/申诉流程 |
| 用户黑名单 | 管理后台黑名单 |

### 6. 推荐系统

| 功能 | 说明 |
|------|------|
| 热门推荐 | 基于播放/点赞/收藏的综合热度 |
| 个性化推荐 | 协同过滤 + 相似度 + 分类/标签偏好 |
| 手动推荐位 | 轮播图、分类推荐、侧边栏等 |
| 推荐效果分析 | 点击率、观看时长等数据分析 |
| 定时更新 | 每日热门计算 |

### 7. 管理后台

| 功能 | 说明 |
|------|------|
| 用户管理 | 角色分配、封禁管理 |
| 角色权限 (RBAC) | 多角色支持，权限细粒度控制 |
| 视频管理与审核 | 含分类显示的审核列表 |
| 评论管理 | 评论审核与删除恢复 |
| 转码队列管理 | 暂停/继续/取消/优先级的可视化 |
| 系统配置 | 运行时配置修改 (含转码参数) |
| 存储管理 | 存储迁移、孤立文件清理、冷存储 |
| 管理员操作日志 | 完整操作审计 |
| 冷存储管理 | 长期未播放视频迁移至冷存储 |

### 8. 视频状态系统

```
转码状态 (status): pending → processing → completed
                                              ↘ failed

审核状态 (is_approved): pending → approved
                                ↘ banned (可申诉: appealing)

播放条件: status == "completed" AND is_approved == "approved" AND visibility == "public"
```

---

## 数据库模型

### 核心数据表 (30+)

| 表名 | 说明 | 关键字段 |
|------|------|---------|
| `users` | 用户表 | id, username, email, is_admin, is_vip, credits |
| `roles` | 角色表 | id, name, permissions |
| `user_roles` | 用户角色关联 | user_id, role_id |
| `videos` | 视频表 | id, title, status, is_approved, visibility, views, duration |
| `categories` | 分类表 | id, name, slug |
| `tags` | 标签表 | id, name, usage_count |
| `video_tags` | 视频标签关联 | video_id, tag_id |
| `comments` | 评论表 | id, content, parent_id, user_id, video_id |
| `video_likes` | 视频点赞 | user_id, video_id, like_type |
| `comment_likes` | 评论点赞 | user_id, comment_id, like_type |
| `user_follows` | 关注关系 | follower_id, followed_id |
| `user_blocks` | 黑名单 | blocker_id, blocked_id |
| `user_video_history` | 观看历史 | user_id, video_id, progress, last_watched |
| `notifications` | 通知表 | recipient_id, type, entity_id, is_read |
| `collections` | 合集表 | id, title, user_id |
| `collection_items` | 合集视频 | collection_id, video_id, order |
| `video_favorites` | 视频收藏 | user_id, video_id |
| `collection_favorites` | 合集收藏 | user_id, collection_id |
| `transcode_tasks` | 转码任务 | id, video_id, status, priority, worker_name |
| `video_recommendations` | 手动推荐 | video_id, recommendation_type, slot_position |
| `recommendation_slots` | 推荐位配置 | slot_name, max_items, strategy |
| `user_video_scores` | 推荐分数缓存 | user_id, video_id, collaborative_score... |
| `daily_trending_videos` | 每日热门 | video_id, trending_date, score |
| `category_trending_videos` | 分类热门 | category_id, video_id, trending_date |
| `system_config` | 系统配置 | key, value (运行时配置) |
| `admin_logs` | 管理员日志 | admin_id, action, target_id |
| `video_shares` | 视频分享 | video_id, token, expires_at |
| `upload_sessions` | 分片上传会话 | id, user_id, file_size, total_chunks |
| `video_audit_logs` | 审核日志 | video_id, operator_id, action |
| `anonymous_view_history` | 匿名播放记录 | anonymous_id, video_id, view_count |
| `view_tokens` | 播放 Token | token, video_id, anonymous_id |

### 实体关系

```
User ──1:N──> Video ──N:1──> Category
         │
         ├──1:N──> Comment ──N:1──> User
         ├──1:N──> VideoLike
         ├──1:N──> Collection ──1:N──> CollectionItem ──N:1──> Video
         └──1:N──> UserVideoHistory

User ──M:N──> User (through UserFollow)
User ──M:N──> User (through UserBlock)
```

---

## 接口文档

### 认证接口 (`/auth`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/token` | 用户登录，获取 JWT token |
| POST | `/users/register` | 用户注册 |
| GET | `/users/me` | 获取当前用户信息 |
| PUT | `/users/me` | 更新用户信息 |
| PUT | `/users/me/password` | 修改密码 |
| PUT | `/users/me/private-videos-password` | 设置私密视频密码 |
| POST | `/users/me/verify-private-password` | 验证私密视频密码 |
| GET | `/users/{user_id}/profile` | 获取用户公开资料 |
| GET | `/users/{user_id}/videos/public` | 获取用户公开视频 |
| GET | `/users/me/videos/private` | 获取用户私密视频 |
| GET | `/users/me/stats` | 获取用户统计数据 |
| GET | `/users/me/videos` | 获取当前用户视频列表 |

### 视频接口 (`/videos`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/videos/upload` | 上传视频 (单文件) |
| GET | `/videos` | 获取视频列表 |
| GET | `/videos/{video_id}` | 获取视频详情 |
| PUT | `/videos/{video_id}` | 更新视频信息 |
| DELETE | `/videos/{video_id}` | 删除视频 (软删除) |
| GET | `/videos/{video_id}/stream` | 获取流地址 |
| GET | `/videos/{video_id}/segment` | 获取 HLS 片段 |
| GET | `/videos/{video_id}/variant/{name}` | 获取指定码率 |
| POST | `/videos/{video_id}/share` | 创建分享链接 |
| GET | `/videos/{video_id}/share` | 获取分享信息 |
| GET | `/videos/shared/{token}` | 通过 token 获取视频 |
| DELETE | `/videos/{video_id}/share` | 删除分享链接 |
| GET | `/videos/{video_id}/view-token` | 获取播放 Token (匿名) |
| POST | `/videos/{video_id}/view` | 记录播放 |
| POST | `/videos/{video_id}/progress` | 保存播放进度 |
| GET | `/videos/{video_id}/progress` | 获取播放进度 |
| POST | `/videos/{video_id}/like` | 点赞/点踩 |
| DELETE | `/videos/{video_id}/like` | 取消点赞 |
| POST | `/videos/{video_id}/favorite` | 收藏/取消收藏 |
| GET | `/videos/{video_id}/comments` | 获取评论列表 |
| POST | `/videos/{video_id}/comments` | 添加评论 |
| GET | `/comments/{comment_id}` | 获取评论详情 |
| DELETE | `/comments/{comment_id}` | 删除评论 |
| POST | `/comments/{comment_id}/like` | 点赞/点踩评论 |
| DELETE | `/comments/{comment_id}/like` | 取消评论点赞 |
| POST | `/videos/{video_id}/thumbnail/regenerate` | 重新生成封面 |
| POST | `/videos/{video_id}/thumbnail/upload` | 上传封面 |
| POST | `/videos/{video_id}/complete` | 标记视频完成 |
| POST | `/videos/{video_id}/upgrade_priority` | VIP 加速转码 |
| POST | `/videos/{video_id}/bump` | 付费插队 |
| POST | `/videos/{video_id}/retry` | 重试转码 |
| GET | `/videos/{video_id}/queue_info` | 获取排队信息 |
| POST | `/videos/{video_id}/appeal` | 申诉下架 |
| GET | `/videos/{video_id}/audit-logs` | 获取审核日志 |

### 字幕接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/videos/{video_id}/subtitles` | 上传字幕文件 |
| GET | `/videos/{video_id}/subtitles` | 获取字幕列表 |
| DELETE | `/videos/{video_id}/subtitles/{lang}` | 删除字幕 |
| POST | `/videos/{video_id}/subtitles/generate` | 自动生成字幕 |
| GET | `/videos/{video_id}/subtitles/task-status` | 字幕任务状态 |

### 分片上传接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/upload-sessions/init` | 初始化分片上传 |
| POST | `/upload-sessions/{id}/chunks/{index}` | 上传分片 |
| POST | `/upload-sessions/{id}/complete` | 完成分片上传 |
| DELETE | `/upload-sessions/{id}` | 取消上传 |
| GET | `/upload-sessions` | 获取上传会话列表 |
| GET | `/upload-sessions/{id}` | 获取上传会话详情 |

### 社交接口 (`/social`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/users/{user_id}/follow` | 关注用户 |
| DELETE | `/users/{user_id}/follow` | 取消关注 |
| DELETE | `/users/{user_id}/follower` | 移除粉丝 |
| GET | `/users/me/following` | 获取关注列表 |
| GET | `/users/me/followers` | 获取粉丝列表 |
| GET | `/users/me/blocks` | 获取黑名单 |
| POST | `/users/{user_id}/block` | 拉黑用户 |
| DELETE | `/users/{user_id}/block` | 取消拉黑 |
| POST | `/videos/{video_id}/favorite` | 收藏视频 |
| POST | `/collections/{id}/favorite` | 收藏合集 |
| GET | `/users/me/favorites/videos` | 获取收藏视频 |
| GET | `/users/me/favorites/collections` | 获取收藏合集 |
| GET | `/users/me/liked/videos` | 获取点赞视频 |
| GET | `/notifications` | 获取通知列表 |
| GET | `/notifications/unread-count` | 获取未读数 |
| POST | `/notifications/read-all` | 标记全部已读 |
| POST | `/notifications/{id}/read` | 标记单条已读 |

### 历史接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/users/me/history` | 获取观看历史 |
| DELETE | `/users/me/history/{video_id}` | 删除单条历史 |
| DELETE | `/users/me/history` | 清空观看历史 |

### 合集接口 (`/collections`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/collections` | 创建合集 |
| PUT | `/collections/{id}` | 更新合集 |
| GET | `/collections` | 获取合集列表 |
| GET | `/collections/{id}` | 获取合集详情 |
| POST | `/collections/{id}/videos` | 添加视频到合集 |
| DELETE | `/collections/{id}/videos/{vid}` | 从合集移除 |
| PUT | `/collections/{id}/reorder` | 排序合集视频 |
| DELETE | `/collections/{id}` | 删除合集 |

### 推荐接口 (`/recommendations`)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/recommendations` | 获取推荐列表 |
| POST | `/recommendations/click` | 记录推荐点击 |
| POST | `/recommendations/watch` | 记录推荐观看 |
| GET | `/admin/recommendations` | 获取手动推荐列表 |
| POST | `/admin/recommendations` | 创建手动推荐 |
| PUT | `/admin/recommendations/{id}` | 更新手动推荐 |
| DELETE | `/admin/recommendations/{id}` | 删除手动推荐 |
| GET | `/admin/recommendation-slots` | 获取推荐位配置 |
| POST | `/admin/recommendation-slots` | 创建推荐位 |
| PUT | `/admin/recommendation-slots/{id}` | 更新推荐位 |
| GET | `/admin/recommendations/analytics` | 推荐效果分析 |
| GET | `/admin/scheduled-tasks` | 定时任务状态 |
| POST | `/admin/recommendations/recompute` | 重新计算推荐 |

### 分类接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/categories` | 获取分类列表 |
| POST | `/categories` | 创建分类 |
| PUT | `/categories/{id}` | 更新分类 |
| DELETE | `/categories/{id}` | 删除分类 |

### 投屏接口 (`/api/cast`)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/devices` | 获取投屏设备 |
| POST | `/play` | 开始投屏 |
| POST | `/stop` | 停止投屏 |

### 管理接口 (`/admin`)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/upload-config` | 获取上传配置 |
| GET | `/stats/system` | 系统统计 |
| GET | `/stats` | 运营统计 |
| GET | `/roles` | 获取角色列表 |
| POST | `/roles` | 创建角色 |
| PUT | `/roles/{id}` | 更新角色 |
| GET | `/config` | 获取系统配置 |
| PUT | `/config` | 更新系统配置 |
| GET | `/env-config` | 获取环境配置 |
| PUT | `/env-config` | 更新环境配置 |
| POST | `/reload` | 重载配置 |
| GET | `/storage/config` | 存储配置 |
| PUT | `/storage/config` | 更新存储配置 |
| GET | `/storage/directories` | 存储目录信息 |
| GET | `/storage/usage` | 存储使用统计 |
| POST | `/storage/migrate` | 迁移存储 |
| GET | `/storage/migrate/{task_id}` | 迁移状态 |
| GET | `/storage/orphans` | 孤立文件列表 |
| GET | `/storage/deleted-videos` | 已删除视频 |
| GET | `/storage/orphan-files` | 孤立文件 |
| POST | `/storage/cleanup` | 清理存储 |
| POST | `/storage/full-cleanup` | 完全清理 |
| GET | `/logs` | 管理员日志 |
| GET | `/transcode/queue` | 转码队列 |
| GET | `/transcode/scan-abnormal` | 扫描异常任务 |
| POST | `/transcode/fix-abnormal` | 修复异常任务 |
| POST | `/transcode/{task_id}/bump` | 提升优先级 |
| POST | `/transcode/{task_id}/cancel` | 取消任务 |
| POST | `/transcode/{task_id}/pause` | 暂停任务 |
| POST | `/transcode/{task_id}/resume` | 恢复任务 |
| GET | `/transcode/config` | 转码配置 |
| PUT | `/transcode/concurrency` | 更新并发数 |
| POST | `/transcode/{video_id}/retry` | 重试转码 |
| POST | `/videos/{video_id}/transcode` | 手动触发转码 |
| GET | `/cold-storage/stats` | 冷存储统计 |
| GET | `/cold-storage/candidates` | 可迁移视频 |
| POST | `/cold-storage/migrate/{id}` | 迁移到冷存储 |
| POST | `/cold-storage/restore/{id}` | 从冷存储恢复 |
| POST | `/cold-storage/migrate-all` | 批量迁移 |
| GET | `/users` | 用户列表 |
| POST | `/users/{id}/status` | 封禁/解封用户 |
| PUT | `/users/{id}/role` | 分配角色 |
| GET | `/videos` | 视频列表 (管理) |
| POST | `/videos/{id}/ban` | 下架视频 |
| POST | `/videos/{id}/approve` | 审核通过 |
| POST | `/videos/{id}/approval` | 更新审核状态 |
| GET | `/comments` | 评论列表 |
| DELETE | `/comments/{id}` | 删除评论 |
| POST | `/comments/{id}/restore` | 恢复评论 |
| POST | `/recommendations/recompute-trending` | 重新计算热门 |

**API 文档**: 启动后访问 `http://localhost:8000/docs` (Swagger UI)

---

## 当前组件环境配置

### 配置文件位置

- 主配置: `app/config.py` (Pydantic BaseSettings)
- 环境变量: `.env` (从 `.env.example` 复制)

### 必需环境变量

```env
# 基础路径
MYVIDEO_ROOT=/data/myvideo

# 数据库
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_USER=myvideo
DATABASE_PASSWORD=xxx
DATABASE_NAME=myvideo_db

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# 安全
SECRET_KEY=your-secret-key
```

### 可选配置项

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ALGORITHM` | HS256 | JWT 算法 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 30 | Token 过期时间 |
| `MAX_UPLOAD_SIZE_MB` | 2048 | 上传大小限制 |
| `TRANSCODE_CONCURRENCY` | 4 | 转码并发数 |
| `TRANSCODE_BUMP_COST` | 5 | 插队消耗积分 |
| `COLD_STORAGE_ENABLED` | false | 启用冷存储 |
| `STORAGE_BACKEND` | local | 存储后端 (local/s3/oss) |

### 路径架构

所有路径相对于 `MYVIDEO_ROOT`:

| 用途 | 路径 |
|------|------|
| 静态文件 | `{MYVIDEO_ROOT}/static/` |
| 上传视频 | `{MYVIDEO_ROOT}/static/videos/uploads/` |
| 转码视频 | `{MYVIDEO_ROOT}/static/videos/processed/` |
| 缩略图 | `{MYVIDEO_ROOT}/static/thumbnails/` |
| 头像 | `{MYVIDEO_ROOT}/static/avatars/` |
| 数据目录 | `{MYVIDEO_ROOT}/data/` |

---

## 部署文档

### 目录结构

```
/data/myvideo/
├── app/
│   ├── main.py              # FastAPI 主应用 + Socket.IO
│   ├── config.py            # 配置管理
│   ├── data_models.py       # SQLModel 数据模型 (30+ 表)
│   ├── database.py         # 数据库连接
│   ├── security.py         # JWT 安全
│   ├── tasks.py            # Celery 任务
│   ├── dependencies.py     # 共享依赖
│   ├── socketio_handler.py # WebSocket 处理器
│   ├── cache_manager.py    # Redis 缓存
│   ├── recommendation_engine.py # 推荐算法
│   ├── init_data.py        # 初始化数据
│   └── routers/            # API 路由模块
│       ├── auth.py         # 认证 (12 端点)
│       ├── videos.py       # 视频 (54 端点)
│       ├── social.py       # 社交 (17 端点)
│       ├── admin.py        # 管理后台 (60+ 端点)
│       ├── collections.py  # 合集 (8 端点)
│       ├── recommendations.py # 推荐 (13 端点)
│       ├── cast.py         # 投屏 (3 端点)
│       └── categories.py   # 分类 (4 端点)
├── static/                  # 前端静态文件
│   ├── index.html          # 首页
│   ├── video.html          # 视频播放页
│   ├── profile.html        # 个人主页
│   ├── dashboard.html      # 创作者工作室
│   ├── upload.html         # 上传页面
│   ├── settings.html       # 账号设置
│   ├── history.html        # 观看历史
│   ├── notifications.html  # 通知中心
│   ├── login.html          # 登录页
│   ├── register.html       # 注册页
│   ├── admin/              # 管理后台页面
│   │   ├── index.html      # 仪表盘
│   │   ├── users.html      # 用户管理
│   │   ├── videos.html     # 视频管理
│   │   ├── comments.html   # 评论管理
│   │   ├── roles.html      # 角色管理
│   │   ├── transcode.html  # 转码队列
│   │   ├── categories.html # 分类管理
│   │   ├── recommendations.html # 推荐管理
│   │   ├── cold_storage.html # 冷存储
│   │   ├── settings.html   # 系统设置
│   │   └── logs.html       # 日志查看
│   └── js/                 # JavaScript
├── docker-compose.yml       # Docker Compose 配置
├── Dockerfile.app          # 应用镜像
├── manage.sh              # 应用管理脚本
├── manage_celery.sh       # Celery 管理脚本
├── celery_config.py       # Celery 配置
├── nginx/conf/nginx.conf  # Nginx 配置
└── .env                   # 环境变量
```

### 快速启动

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env 填入实际配置

# 2. 安装依赖
pip install -r app/requirements.txt

# 3. 启动 FastAPI 应用
./manage.sh start

# 4. 启动 Celery Worker (另一个终端)
./manage_celery.sh start

# 5. 验证
curl http://localhost:8000/
```

### Docker Compose 部署

```bash
# 启动所有服务 (PostgreSQL + Redis + Nginx + App + Celery)
docker-compose -f docker-compose.yml -f docker-compose.app.yml up -d

# 多实例部署
docker-compose -f docker-compose.yml -f docker-compose.app.yml up -d --scale app=2 --scale celery_worker=2
```

### 管理脚本

```bash
./manage.sh start|stop|restart|logs     # FastAPI 主应用
./manage_celery.sh start|stop|restart    # Celery Worker
```

### 分布式部署架构

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
│  FastAPI      │      │ Celery        │      │ Celery        │
│  (8000)      │      │ Worker #1     │      │ Worker #2     │
└───────────────┘      └───────────────┘      └───────────────┘
        │                         │                         │
        ▼                         ▼                         ▼
   Nginx 反向代理            FFmpeg 转码                FFmpeg 转码
```

### Nginx 配置要点

```nginx
# 大文件上传
client_max_body_size 5000M;

# WebSocket 支持
location /socket.io {
    proxy_pass http://myvideo_app;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 7d;
    proxy_buffering off;
}
```

### WebSocket 协议

| 事件 | 方向 | 说明 |
|------|------|------|
| `connect` | Client→Server | 连接时传递 `auth.token` |
| `disconnect` | Client→Server | 断开连接 |
| `transcode_progress` | Server→Client | 转码进度推送 |
| `ping` | Client→Server | 心跳检测 |

### Celery 定时任务

| 任务 | 执行时间 | 说明 |
|------|----------|------|
| `compute-daily-trending` | 每天 02:00 | 计算每日热门视频 |
| `compute-category-trending` | 每天 03:00 | 计算分类热门视频 |
| `cold-storage-migration-daily` | 每天 01:00 | 冷存储迁移扫描 |
| `transcode-aging-hourly` | 每小时 | 转码队列 aging 更新 |
| `zombie-ffmpeg-cleanup` | 每 10 分钟 | 清理僵尸 ffmpeg |

### 常见问题

**Worker 无法连接 Redis**
```bash
redis-cli -h <REDIS_HOST> ping
```

**转码失败**
```bash
ffmpeg -version
tail -f /data/myvideo/celery_worker.log
```

**上传失败 413** - 检查 Nginx `client_max_body_size`

---

## 前端页面

| 页面 | 路径 | 说明 |
|------|------|------|
| 首页 | `/static/index.html` | 轮播推荐、分类导航、热门视频 |
| 视频播放 | `/static/video.html?id=xxx` | 播放器、字幕、评论、键盘快捷键 |
| 个人主页 | `/static/profile.html?id=xxx` | 用户信息、视频、粉丝 |
| 创作者工作室 | `/static/dashboard.html` | 统计、视频管理、审核状态 |
| 上传视频 | `/static/upload.html` | 分片上传、WebSocket 进度 |
| 账号设置 | `/static/settings.html` | 个人信息、密码、私密空间 |
| 观看历史 | `/static/history.html` | 历史记录、续播 |
| 通知中心 | `/static/notifications.html` | 消息通知 |
| 登录 | `/static/login.html` | 登录页 |
| 注册 | `/static/register.html` | 注册页 |
| 管理后台 | `/static/admin/index.html` | 仪表盘、用户、视频、评论 |

---

## 许可证

MIT
