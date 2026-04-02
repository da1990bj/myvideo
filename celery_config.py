"""
Celery 独立配置文件

与 docker-compose.yml 配合使用：
- docker-compose run --rm celery_worker celery -A celery_config worker --loglevel=info
- docker-compose run --rm celery_beat celery -A celery_config beat --loglevel=info
"""
from celery import Celery
from celery.schedules import crontab
import os

# 从环境变量读取配置
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_DB = os.getenv("REDIS_DB", "0")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

if REDIS_PASSWORD:
    CELERY_BROKER_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
else:
    CELERY_BROKER_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Shanghai'

# 任务配置
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 3600 * 2  # 2小时超时
CELERY_RESULT_EXPIRES = 3600 * 24  # 24小时后结果过期

# Worker 配置
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # 公平分发，避免一个任务独占
CELERY_WORKER_MAX_TASKS_PER_CHILD = 100  # 每个 worker 处理 100 个任务后重启，防止内存泄漏

# 定时任务
CELERY_BEAT_SCHEDULE = {
    'compute-recommendations-daily': {
        'task': 'tasks.compute_all_recommendation_scores',
        'schedule': crontab(hour=2, minute=0),  # 每天凌晨2点
        'options': {'queue': 'default'}
    },
    'cold-storage-migration-daily': {
        'task': 'tasks.cold_storage_migration_task',
        'schedule': crontab(hour=1, minute=0),  # 每天凌晨1点
        'options': {'queue': 'default'}
    },
}

# 创建 Celery app
celery_app = Celery('myvideo')
celery_app.config_from_object(__name__)

# 导入任务模块
celery_app.autodiscover_tasks(['tasks'])
