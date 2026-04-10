# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MyVideo v1.13 is a full-featured video on-demand platform built with Python/FastAPI. It includes video upload/transcoding/streaming, user authentication, social features (comments, likes, follows, subtitle support), RBAC permission system, and an admin dashboard.

**Tech Stack**: FastAPI, SQLModel (PostgreSQL), Redis, Celery, FFmpeg, python-socketio (WebSocket), native HTML5/JS frontend with hls.js.

---

## Configuration

All configuration is centralized in `app/config.py` using Pydantic `BaseSettings`.

### Usage
```python
from config import settings

# Database
settings.DATABASE_URL

# Paths (Path objects)
settings.BASE_DIR           # Project root
settings.STATIC_DIR         # Static files directory
settings.UPLOADS_DIR        # Video uploads
settings.THUMBNAILS_DIR      # Thumbnails

# Path conversion helpers
settings.fs_path("/static/thumbnails/image.jpg")  # URL path → filesystem path
settings.url_path("thumbnails/image.jpg")          # Relative → URL path
```

### Environment Variables
Copy `.env.example` to `.env` and configure:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_HOST` | localhost | PostgreSQL host |
| `DATABASE_PORT` | 5432 | PostgreSQL port |
| `DATABASE_USER` | myvideo | Database user |
| `DATABASE_PASSWORD` | myvideo_password | Database password |
| `DATABASE_NAME` | myvideo_db | Database name |
| `REDIS_HOST` | localhost | Redis host |
| `REDIS_PORT` | 6379 | Redis port |
| `REDIS_PASSWORD` | (none) | Redis password |
| `SECRET_KEY` | (insecure default) | JWT secret key |
| `MYVIDEO_ROOT` | (auto-detected) | Project root override |

### Path Architecture
All paths are relative to `BASE_DIR` (auto-detected from `config.py` location):
- `STATIC_DIR`: `{BASE_DIR}/static`
- `UPLOADS_DIR`: `{BASE_DIR}/static/videos/uploads`
- `THUMBNAILS_DIR`: `{BASE_DIR}/static/thumbnails`
- `PROCESSED_DIR`: `{BASE_DIR}/static/videos/processed`
- `AVATARS_DIR`: `{BASE_DIR}/static/avatars`

---

## Commands

### Application Management
```bash
./manage.sh start    # Start FastAPI app (uvicorn on port 8000)
./manage.sh stop     # Stop app
./manage.sh restart  # Restart app
./manage.sh logs     # View server.log
```

### Celery Worker (video transcoding)
```bash
./manage_celery.sh start   # Start Celery worker
./manage_celery.sh stop    # Stop worker
./manage_celery.sh restart # Restart worker
```

### Development
```bash
# Python environment at /home/da/anaconda3/envs/myvideo/bin/python3.10
# Requirements: /data/myvideo/app/requirements.txt
```

---

## Architecture

### Backend Structure (`app/`)

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app + Socket.IO server (`socketio_app`), CORS, JWT auth, all API routes |
| `config.py` | **Centralized settings** using Pydantic BaseSettings |
| `data_models.py` | SQLModel ORM models (User, Video, Comment, Role, Notification, etc.) |
| `database.py` | DB engine, session management, `get_session` dependency |
| `security.py` | JWT token creation/validation, password hashing |
| `tasks.py` | Celery tasks (`transcode_video_task`) — runs FFmpeg for HLS transcoding |
| `socketio_handler.py` | `ConnectionManager` class — WebSocket connection pool, progress push |
| `recommendation_engine.py` | Video recommendation scoring and caching |
| `cache_manager.py` | Redis-based caching for recommendations |
| `init_data.py` | Startup initialization (categories, recommendation slots) |

### Frontend Structure (`static/`)

| Path | Purpose |
|------|---------|
| `index.html` | Homepage with recommendations |
| `video.html` | Video player page — HLS playback, subtitles, comments, likes, keyboard shortcuts, playback position memory |
| `profile.html` | User profile — videos, followers, following |
| `dashboard.html` | Creator dashboard — statistics and analytics |
| `upload.html` | Video upload with WebSocket progress |
| `settings.html` | Account settings |
| `history.html` | Watch history |
| `notifications.html` | Notifications |
| `login.html` / `register.html` | Authentication |
| `admin/` | Admin dashboard pages |
| `js/nav.js` | Navigation/auth state management |
| `js/app.js` | Shared utilities (API client, state management) |

### Data Flow

1. User uploads video → `POST /videos/upload` in `main.py`
2. Celery task `transcode_video_task` queued → FFmpeg transcodes to HLS
3. During transcoding, `socketio_handler.push_progress()` emits `transcode_progress` event to connected creator
4. Frontend `upload.html` listens for WebSocket events to update progress bars in real-time
5. HLS streams served via `/static/videos/processed/{video_id}/playlist.m3u8`
6. Playback position auto-saved to localStorage every 5 seconds and on page close

### WebSocket Protocol
- Events: `connect`, `disconnect`, `transcode_progress`, `transcode_progress_batch`, `ping`
- Auth: JWT token passed via `auth.token` on connection
- Clients: `upload.html` (creator studio for upload progress)

---

## Key Patterns

### API Route Handler Pattern
```python
@app.post("/videos/{video_id}/like")
async def like_video(video_id: UUID, session: Session = Depends(get_session), token: str = Depends(oauth2_scheme)):
    user = verify_jwt(token)
    # ... logic
```

### WebSocket Progress Push
```python
# From tasks.py, during FFmpeg:
if push_progress_callback:
    push_progress_callback(str(video.id), global_percent)

# socketio_handler receives and broadcasts:
await manager.push_progress(sio, user_id, video_id, progress, status)
```

### Database Session Pattern
```python
with Session(engine) as session:
    video = session.exec(select(Video).where(Video.id == video_id)).first()
    # ... modifications ...
    session.add(video)
    session.commit()
```

### Settings-Based Path Pattern
```python
# Instead of hardcoded paths:
# OLD: "/data/myvideo/static/thumbnails/image.jpg"
# NEW: settings.THUMBNAILS_DIR / "image.jpg"

# URL to filesystem conversion:
# OLD: path.replace("/static", "/data/myvideo/static")
# NEW: settings.fs_path("/static/thumbnails/image.jpg")
```

---

## File Storage Paths (via settings)

| Purpose | Path (via settings) |
|---------|---------------------|
| Uploaded videos | `settings.UPLOADS_DIR / "{uuid}.mp4"` |
| Transcoded HLS | `settings.PROCESSED_DIR / "{video_id}/"` |
| Thumbnails | `settings.THUMBNAILS_DIR / "{filename}.jpg"` |
| Avatars | `settings.AVATARS_DIR / "{user_id}.jpg"` |

---

## Notes

- **Configuration**: All settings in `app/config.py` - no hardcoded values
- WebSocket uses python-socketio with ASGI adapter (`socketio.AsyncServer` with `async_mode='asgi'`)
- The app mounts at `/static` for serving frontend files via `settings.STATIC_DIR`
- JWT tokens expire after `settings.ACCESS_TOKEN_EXPIRE_MINUTES`
- Admin routes require `admin` role permission checked via dependency
- The recommendation system uses a scoring cache (`RecommendationCache` in `cache_manager.py`)

---

## Video Status System (v1.15+)

### Video Fields

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `status` | string | `pending`, `processing`, `completed`, `failed` | **Transcoding status only** |
| `is_approved` | string | `pending`, `approved`, `banned`, `appealing` | **Audit status** |
| `is_deleted` | boolean | `true`, `false` | Soft delete flag |
| `visibility` | string | `public`, `private`, `unlisted` | Video visibility |

### Status Transitions

```
Transcoding (status):
  pending → processing → completed
                        ↘ failed

Audit (is_approved):
  pending → approved    (admin approves)
           ↘ banned    (admin bans)
           ↘ appealing (user appeals)
```

### Playback Condition
A video is playable only when: `status == "completed" AND is_approved == "approved" AND visibility == "public"`

### Key API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/videos/{id}/approve` | POST | Approve video (sets `is_approved="approved"`) |
| `/admin/videos/{id}/ban` | POST | Ban video (sets `is_approved="banned"`) |
| `/admin/videos/{id}/approval` | POST | Update audit status (pending/approved/banned/appealing) |
| `/videos/{id}/appeal` | POST | User appeals ban (sets `is_approved="appealing"`) |

### Frontend Pages

- `static/admin/videos.html` - Admin video management with audit status display
- `static/dashboard.html` - Creator dashboard with video status and audit info
- `static/video.html` - Video player checks `playable` field from API
