# MyVideo - 功能完整的视频点播平台 v2.0

## 1. 项目概述 (Project Overview)
MyVideo 是一个基于 Python/FastAPI 构建的功能完整、生产级别的视频点播平台。集成了高性能视频转码、流媒体播放、社交互动、内容管理、用户权限控制等核心功能。项目采用微服务架构思想设计，支持高并发视频处理与分发，同时提供完整的后端管理系统。

**v2.0 版本特性**: RBAC权限系统完整实现、管理后台全功能、高级内容管理、用户社交完整闭环。

### 1.1 技术栈 (Tech Stack)
- **后端框架**: Python FastAPI (High-performance async API)
- **数据库**: PostgreSQL (SQLModel ORM, 支持强类型)
- **缓存与消息队列**: Redis
- **异步任务**: Celery (视频转码、后台作业处理)
- **多媒体处理**: FFmpeg (HLS 切片, 多码率转码, 缩略图生成)
- **前端**: 原生 HTML5/CSS3 + ES6 JavaScript, Hls.js (自适应码率流媒体)
- **部署**: Docker Compose, Nginx反向代理
- **权限系统**: RBAC(基于角色的访问控制) + 权限检查

---

## 2. 核心模块详解 (Module Breakdown)

### 2.1 视频核心模块 (Video Core)
负责视频全生命周期管理，从上传到分发。
- **视频上传**: 支持大文件上传，自动生成 UUID 文件名进行去重。
- **智能转码**: 后台异步任务队列处理，将源视频转码为 HLS (m3u8) 格式，支持多码率自适应（480p/720p/1080p）。
- **流媒体播放**: 集成 hls.js 播放器，支持自适应码率切换、清晰度选择。
- **封面管理**: 支持自动截取视频帧作为封面，或用户上传自定义封面。
- **视频状态管理**: 包含上传中、处理中、已发布、草稿、下架等多种状态。
- **可见性控制**: 支持 public/private 等多种可见性设置。

### 2.2 用户体验模块 (User Experience)
提升用户观看沉浸感与便捷性。
- **断点续播 (Resume Playback)**: 系统自动记录每位用户的观看进度，跨设备同步，再次打开视频时自动跳转至上次观看时间点（>5秒触发）。
- **观看历史 (History)**: 完整的用户观看足迹记录，按时间倒序排列，可快速重新访问。
- **完播率统计 (Smart Completion)**: 采用”有效完播”逻辑，当用户观看进度超过 90% 时自动计入完播数据，过滤片尾无效时长。
- **视频统计**: 实时浏览量、完播数、点赞数等多维度数据统计。

### 2.3 社交互动系统 (Social Interaction)
构建完整的社区氛围，连接创作者与观众。
- **评论系统 (Nested Comments)**: 支持视频下方发表评论，支持嵌套回复，实时展示用户信息和头像。
- **评论点赞**: 用户可对评论和回复进行点赞，计算热度排序。
- **软删除机制**: 用户和管理员均可删除评论，保留数据完整性用于审计。
- **关注机制 (Follow System)**: 用户可以关注喜欢的创作者，支持取消关注，创作者拥有粉丝管理能力。
- **用户屏蔽 (Block)**: 用户可以屏蔽不感兴趣的用户内容。
- **点赞/踩 (Like/Dislike)**: 对视频内容进行态度表达，影响推荐算法。
- **收藏系统 (Favorites)**: 用户可收藏喜欢的视频和播放列表，方便后续查看。
- **合集功能 (Collections)**: 创作者可创建视频合集/播放列表，用户可收藏合集。
- **通知中心 (Notification Center)**:
    - 聚合”关注”、”评论”、”点赞”、”回复”等多类型消息。
    - 导航栏实时红点提醒（轮询机制）。
    - 支持一键已读、批量管理和跳转直达。
    - 完整的通知历史记录。

### 2.4 内容与标签管理 (Content & Tagging)
完善的内容分类和标签系统。
- **分类系统 (Categories)**: 视频可属于多个预定义分类，便于浏览和推荐。
- **标签系统 (Tags)**:
    - 维护全局标签池，避免重复。
    - 标签使用计数统跑，便于热门标签展示。
    - 支持标签搜索和内容发现。
- **视频审核日志**: 记录所有视频的上架、下架、申诉历史。

### 2.5 用户与权限系统 (User & Auth)
完整的身份认证和权限控制。
- **身份认证**: 基于 JWT (JSON Web Tokens) 的无状态认证机制，支持刷新令牌。
- **用户注册与登录**: 邮箱注册、用户名唯一性验证、密码安全加密。
- **个人资料管理**:
    - 用户头像上传和管理。
    - 个性化简介編輯。
    - 邮箱和密码修改。
- **个人主页**: 展示用户头像、简介、粉丝/关注数及公开投稿视频。
- **创作者工作室**: 专属后台，管理上传的视频内容、查看统计数据。
- **RBAC 权限系统** (v2.0 新增):
    - 基于角色的访问控制，支持自定义角色。
    - 灵活的权限分配机制。
    - 管理员、编辑、内容审核等多种角色预设。
    - 权限检查贯穿所有关键操作。


### 2.6 后端管理系统 (Backend Management System) - v2.0 新增
完整的运营管理后台，支持内容治理和用户管理。
- **用户管理**: 查看所有用户、编辑用户角色权限、禁用/启用用户账户。
- **角色管理**: 创建自定义角色、灵活分配权限、权限预设模板。
- **视频管理和审核**:
    - 浏览所有视频及其详细状态。
    - 视频下架和恢复功能（记录下架原因）。
    - 视频审核日志完整追踪。
- **评论管理**: 查看和删除不当评论，记录管理操作。
- **系统日志**: 审计所有管理员操作，追踪信息修改和权限变更。
- **系统配置**: UI支持修改系统参数和政策设置。

---

## 3. 数据库架构 (Database Schema)

项目数据库包含以下主要实体和关系：
- **用户** (Users): 包含用户信息、头像、个人资料、角色关联。
- **角色权限** (Roles, AdminLogs): RBAC系统的核心，完整的权限审计日志。
- **视频** (Videos, VideoAuditLog, VideoTag): 视频内容及其状态、审核历史、标签关联。
- **互动数据** (Comments, CommentLike, VideoLike, UserFollow, UserBlock): 完整的社交关系数据。
- **通知** (Notifications): 实时通知消息系统。
- **用户行为** (UserVideoHistory, VideoFavorite): 观看历史、收藏记录。
- **内容组织** (Collections, CollectionItem, CollectionFavorite, Category, Tag): 分类、标签、合集管理。
- **系统配置** (SystemConfig): 平台级配置存储。

---

## 4. API 端点概览 (API Endpoints Overview)

主要API大致分为以下几类：

### 用户相关
- POST `/register` - 用户注册
- POST `/token` - 用户登录
- GET `/users/{user_id}` - 获取用户信息
- PUT `/users/me` - 更新个人资料
- POST `/users/me/avatar` - 上传头像

### 视频相关
- POST `/videos/upload` - 上传视频
- GET `/videos` / `/videos/{video_id}` - 查看视频列表和详情
- PUT `/videos/{video_id}` - 编辑视频信息
- DELETE `/videos/{video_id}` - 删除视频
- POST `/videos/{video_id}/watch` - 记录观看进度

### 社交互动
- POST `/videos/{video_id}/like` - 对视频点赞
- POST `/videos/{video_id}/comments` - 发表评论
- POST `/users/{user_id}/follow` - 关注用户
- POST `/users/{user_id}/block` - 屏蔽用户

### 通知
- GET `/notifications` - 获取通知列表
- PUT `/notifications/{notification_id}/read` - 标记为已读
- PUT `/notifications/read-all` - 一键已读

### 收藏和合集
- POST `/videos/{video_id}/favorite` - 收藏视频
- GET `/favorites/videos` - 获取收藏的视频
- POST `/collections` - 创建合集
- POST `/collections/{collection_id}/items` - 添加视频到合集

### 管理后台
- GET `/admin/users` - 用户管理列表
- POST `/admin/roles` - 创建角色
- PUT `/admin/users/{user_id}/role` - 分配用户角色
- GET `/admin/videos` - 视频审核列表
- PUT `/admin/videos/{video_id}/ban` - 下架视频
- GET `/admin/logs` - 管理员操作日志

---

## 5. 部署与运维 (Deployment & Operations)

### 5.1 目录结构
```bash
/data/myvideo/
├── app/                      # 后端核心代码 (FastAPI + Celery)
│   ├── main.py              # 主应用和路由
│   ├── data_models.py       # SQLModel 数据模型定义
│   ├── database.py          # 数据库连接和初始化
│   ├── security.py          # JWT 和密码加密
│   ├── tasks.py             # Celery 异步任务
│   ├── utils.py             # 工具函数
│   ├── init_data.py         # 初始数据加载
│   └── test_api.py          # API 测试脚本
├── static/                  # 前端静态资源
│   ├── html/               # HTML 页面
│   ├── js/                 # JavaScript 脚本
│   ├── css/                # 样式表
│   ├── admin/              # 管理后台 (v2.0 新增)
│   ├── videos/             # 视频存储目录
│   ├── thumbnails/         # 视频缩略图
│   └── avatars/            # 用户头像
├── nginx/                  # Nginx 网关配置
├── postgres/               # 数据库数据卷
├── redis/                  # Redis 数据卷
├── docker-compose.yml      # 容器编排文件
└── manage.sh               # 服务管理脚本
```

### 5.2 管理脚本 (manage.sh)
项目内置了快捷管理脚本，用于宿主机进程管理：
```bash
./manage.sh start   # 启动所有服务
./manage.sh stop    # 停止所有服务
./manage.sh restart # 重启所有服务
./manage.sh logs    # 查看日志
```

### 5.3 环境要求
- Docker 和 Docker Compose
- 宿主机至少 2GB RAM、2 核 CPU
- 存储空间根据视频数量：每 1GB 视频库需约 2-3GB（原始 + 转码版本）

### 5.4 启动步骤
1. 克隆仓库：`git clone <repository>`
2. 进入目录：`cd /data/myvideo`
3. 启动服务：`./manage.sh start`
4. 访问前端：`http://localhost`
5. 访问管理后台：`http://localhost/admin` (需管理员账户)

---

## 6. 版本历史与功能演进 (Version History)

v2.0 是项目的一个重要里程碑，在 v1.x 基础上增加了完整的企业级管理系统和权限控制。

- **v2.0** (Mar 2026):
  - ✅ 完整的 RBAC 权限系统
  - ✅ 功能完整的后端管理系统（用户、角色、视频、评论、日志管理）
  - ✅ 视频审核流程和日志追踪
  - ✅ 管理员行为审计日志
  - ✅ 系统级别配置管理
  - ✅ 平台级统计数据

- **v1.5** (Dec 2025):
  - 标签系统重构和优化
  - 用户个人资料 bug 修复
  - 信息完播率统计优化

- **v1.4** (Nov 2025):
  - 管理员面板初版
  - 基础用户和视频管理
  - 系统日志记录

- **v1.3** (Oct 2025):
  - 通知中心完整实现
  - 个人主页数据统计

- **v1.2** (Sep 2025):
  - 观看历史功能
  - 断点续播实现
  - 评论系统（基础版本）
  - 完播率算法优化

- **v1.1** (Aug 2025):
  - 基础视频上传、转码、播放
  - 用户系统（注册、登录、个人资料）
  - JWT 认证机制

---

## 7. 开发与贡献 (Development)

### 项目技术亮点
1. **高性能异步设计**: FastAPI + asyncio 实现高并发处理
2. **完整的权限系统**: RBAC + 权限检查 + 审计日志
3. **微服务思想**: Celery 后台任务队列解耦，支持水平扩展
4. **数据完整性**: 软删除 + 审计日志 + 事务支持
5. **现代化前端**: 无框架依赖的原生 JavaScript，高效轻量
6. **流媒体优化**: HLS 自适应码率，提升用户体验

### 核心依赖
- fastapi 0.100+
- sqlmodel 0.0.13+
- celery 5.3+
- pydantic 2.0+
- python-jose[cryptography]

---

## 8. 常见问题 (FAQ)

**Q: 如何上传视频到平台？**
A: 登录后进入"创作者工作室"，点击"上传视频"，选择文件并填写标题、描述、分类等信息。系统会自动进行转码处理（可能需要数分钟至数小时）。

**Q: 断点续播的触发条件是什么？**
A: 当用户观看超过 5 秒时，系统自动保存进度。下次打开该视频时，会自动跳转到上次位置。

**Q: 如何成为管理员？**
A: 管理员需要在数据库直接添加或由现有管理员分配相应角色。生产环境应使用安全的角色分配流程。

**Q: 如何处理不当内容？**
A: 管理员可在管理后台进行视频下架、评论删除等操作，所有操作都会记录在审计日志中。

**Q: 视频转码花费多长时间？**
A: 取决于视频大小和宿主机性能。通常 100MB 视频需要 5-15 分钟。可在 Celery 任务队列监控进度。

---

## 9. 许可与致谢 (License & Credits)

本项目采用 MIT 许可证。如有任何问题或建议，欢迎提交 Issue 或 Pull Request。

**技术栈致谢**:
- Sebastián Ramírez (FastAPI 创作者)
- SQLAlchemy 和 SQLModel 社区
- FFmpeg 项目
- HLS.js 项目


