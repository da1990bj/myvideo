#!/bin/bash

# Configuration
APP_DIR="/data/myvideo/app"
PYTHON="/home/da/anaconda3/envs/myvideo/bin/python3.1"
CELERY_BIN="/home/da/anaconda3/envs/myvideo/bin/celery"
LOG_FILE="/data/myvideo/celery_worker.log"
PID_FILE="/data/myvideo/celery.pid"

start() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null; then
            echo "Celery Worker is already running (PID: $PID)"
            return
        else
            echo "Stale PID file found. Removing..."
            rm "$PID_FILE"
        fi
    fi

    echo "Starting Celery Worker..."
    cd "$APP_DIR" || exit 1

    # Start Celery Worker
    # -A tasks.celery_app points to the Celery instance in app/tasks.py
    nohup "$CELERY_BIN" -A tasks.celery_app worker --loglevel=info > "$LOG_FILE" 2>&1 &

    PID=$!
    echo "$PID" > "$PID_FILE"
    echo "Celery Worker started with PID $PID. Logs: $LOG_FILE"
}

stop() {
    echo "Stopping Celery Worker..."
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null; then
            kill "$PID"
            echo "Sent SIGTERM to PID $PID"

            # Wait loop
            for i in {1..5}; do
                if ! ps -p "$PID" > /dev/null; then
                    break
                fi
                sleep 1
            done
        fi
        rm "$PID_FILE"
    fi

    # Fallback cleanup
    pkill -f "celery worker"

    echo "Celery Worker stopped."
}

restart() {
    stop
    sleep 2
    start
}

case "$1" in
    start) start ;;
    stop) stop ;;
    restart) restart ;;
    *) echo "Usage: $0 {start|stop|restart}" ;;
esac
