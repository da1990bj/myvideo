# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- **Start API Server**: `./manage.sh start` (Runs Uvicorn on port 8000 with reload)
- **Stop API Server**: `./manage.sh stop`
- **Restart API Server**: `./manage.sh restart`
- **Start Celery Worker**: `./manage_celery.sh start` (Processes async tasks like video transcoding)
- **Stop Celery Worker**: `./manage_celery.sh stop`
- **Start Infrastructure**: `docker-compose up -d` (Starts Postgres, Redis, Nginx)
- **Stop Infrastructure**: `docker-compose down`
- **View Server Logs**: `tail -f server.log`
- **View Worker Logs**: `tail -f celery_worker.log`
- **Install Dependencies**: `pip install -r app/requirements.txt`
- **Run Manual Test**: `python app/test_api.py <TOKEN>` (Requires a valid JWT token)

## Architecture

- **Backend Framework**: Python FastAPI (`app/main.py`)
- **Database**: PostgreSQL with SQLModel ORM (`app/data_models.py`)
- **Async Tasks**: Celery with Redis broker (`app/tasks.py`)
- **Frontend**: Vanilla HTML5/CSS3/ES6 JavaScript (`static/`) served via Nginx
- **Video Streaming**: HLS (m3u8) format, transcoded by FFmpeg via Celery tasks
- **Authentication**: JWT (JSON Web Tokens) stateless authentication
- **Deployment**:
    - **Docker**: Manages PostgreSQL, Redis, and Nginx containers (`docker-compose.yml`)
    - **Local Process**: FastAPI app and Celery worker run directly on the host machine, managed by shell scripts (`manage.sh`, `manage_celery.sh`)

## Directory Structure

- `app/`: Core backend code (FastAPI + Celery)
- `static/`: Frontend assets (HTML, JS, CSS, uploaded videos)
- `nginx/`: Nginx configuration
- `postgres/` & `redis/`: Data volumes and configurations
- `manage.sh` & `manage_celery.sh`: Service management scripts

## Key Features & Implementations

- **Video Processing**: Videos are uploaded, then processed asynchronously by Celery to generate HLS streams and thumbnails.
- **Playback**: Uses `hls.js` for adaptive bitrate streaming.
- **User System**: Includes auth, profiles, watch history, and social features (comments, likes, follows).
- **Notification**: Polling-based notification system for user interactions.
