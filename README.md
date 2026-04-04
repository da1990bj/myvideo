# MyVideo v1.1 - 视频点播平台

## 项目概述

MyVideo 是一个基于 Python/FastAPI 构建的完整视频点播平台，支持视频上传、转码、播放，以及用户社交互动、内容管理和权限控制。

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端框架 | FastAPI (Python 3.10) |
| 数据库 | PostgreSQL + SQLModel ORM |
| 缓存/消息队列 | Redis |
| 异步任务 | Celery + Redis Broker |
| 视频处理 | FFmpeg (HLS 转码) |
| 前端 | 原生 HTML5/CSS3/JS + hls.js |
| WebSocket | python-socketio |
| 部署 | Docker Compose + Nginx |

## 核心功能

### 视频模块
- 视频上传（支持 MKV/MP4/MOV 等格式）
- 异步转码为 HLS 自适应码率
- 实时转码进度推送（WebSocket）
- 视频封面自动截取
- 断点续播、观看历史
- 完播率统计
- **转码队列管理** - 查看所有转码任务状态
- **转码暂停/继续/取消** - 支持中途暂停、恢复和取消任务
- **转码节点显示** - 显示任务在哪台 Worker 节点执行
- **付费插队** - 用户可消耗积分加速转码优先级
- **排队信息** - 用户可查看排队位置和预估等待时间

### 用户与社交
- JWT 认证（注册、登录、修改密码）
- 个人主页（粉丝、关注、投稿统计）
- 关注/粉丝系统
- 拉黑功能
- 点赞、收藏
- 评论系统（嵌套回复）
- 通知中心

### 内容管理
- 合集/播放列表
- 分类与标签系统
- 视频审核（上架/下架）
- 用户黑名单管理

### 推荐系统
- 热门推荐（基于播放/点赞/收藏）
- 个性化推荐（协同过滤）
- 分类/标签相似推荐
- 手动推荐位管理
- 推荐效果分析

### 管理后台
- 用户管理（角色分配、封禁）
- 角色权限管理（多角色支持）
- 视频管理与审核（含分类显示）
- 评论管理
- 转码队列管理（暂停/继续/取消）
- 系统配置（运行时修改，含转码参数配置）
- 管理员操作日志

## 目录结构

```
/data/myvideo/
├── app/
│   ├── main.py              # FastAPI 主应用 + WebSocket
│   ├── config.py            # 配置管理
│   ├── data_models.py       # SQLModel 数据模型
│   ├── database.py         # 数据库连接
│   ├── security.py         # JWT 加密
│   ├── tasks.py            # Celery 异步任务
│   ├── dependencies.py     # 共享依赖（权限检查）
│   ├── routers/            # API 路由模块
│   │   ├── auth.py         # 认证相关
│   │   ├── videos.py        # 视频相关
│   │   ├── collections.py   # 合集相关
│   │   ├── social.py        # 社交相关
│   │   ├── admin.py         # 管理后台
│   │   └── recommendations.py # 推荐系统
│   ├── socketio_handler.py # WebSocket 处理器
│   ├── cache_manager.py    # Redis 缓存
│   └── recommendation_engine.py # 推荐算法
├── static/                  # 前端静态文件
│   ├── index.html          # 首页
│   ├── video.html          # 视频播放页
│   ├── profile.html        # 个人主页
│   ├── dashboard.html       # 创作者工作室
│   ├── upload.html         # 上传页面
│   ├── settings.html       # 账号设置
│   ├── history.html        # 观看历史
│   ├── notifications.html  # 通知中心
│   ├── admin/              # 管理后台
│   └── js/                 # JavaScript
├── nginx/                   # Nginx 配置
├── DEPLOYMENT.md           # 部署文档
└── ARCHITECTURE.md         # 架构图

```

## 快速启动

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env 填入数据库、Redis 等配置

# 2. 启动应用
./manage.sh start

# 3. 启动 Celery Worker（转码等后台任务）
./manage_celery.sh start

# 4. 访问
# 前端: http://localhost
# API:  http://localhost:8000
```

## 管理脚本

```bash
./manage.sh start|stop|restart|logs     # FastAPI 主应用
./manage_celery.sh start|stop|restart     # Celery Worker
```

## API 文档

启动后访问：`http://localhost:8000/docs`（Swagger UI）

## 主要依赖

```
fastapi>=0.100
sqlmodel>=0.0.13
celery>=5.3
redis>=4.0
python-socketio>=5.9
ffmpeg-python
python-jose[cryptography]
passlib[bcrypt]
pydantic>=2.0
uvicorn
```

## 更新日志

### v1.1 (2026-04-04)
- **转码队列优化**
  - 转码中任务显示执行节点名称
  - 支持暂停/继续/取消转码任务
  - 取消时自动清理缓存文件
  - 后台显示转码进度条
- **积分插队系统**
  - 用户可消耗积分对付费加速任务插队
  - 可配置的插队成本（默认5积分）
  - 后台可配置转码并发数、优先级参数
- **用户体验优化**
  - 创作者中心显示排队位置和预估等待时间
  - 失败任务显示重试按钮（用户限制1次，管理员不限）
  - 视频管理列表显示分类信息
- **其他优化**
  - 删除视频时自动清理关联数据
  - 修复任务状态同步问题

### v1.0 (2026-03)
- 初始版本发布
- 视频上传、转码、播放
- 用户社交功能（点赞、收藏、关注）
- 评论系统
- 管理后台
- 推荐系统

## 许可证

MIT
