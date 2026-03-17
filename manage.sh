#!/bin/bash

# Configuration
APP_DIR="/data/myvideo/app"
PYTHON="/home/da/anaconda3/envs/myvideo/bin/python3.10"
UVICORN_BIN="/home/da/anaconda3/envs/myvideo/bin/uvicorn"
LOG_FILE="/data/myvideo/server.log"
PID_FILE="/data/myvideo/app.pid"

start() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null; then
            echo "App is already running (PID: $PID)"
            return
        else
            echo "Stale PID file found. Removing..."
            rm "$PID_FILE"
        fi
    fi

    echo "Starting app..."
    cd "$APP_DIR" || exit 1

    # Run with nohup in background
    # Use socketio_app to properly handle WebSocket connections
    nohup "$PYTHON" "$UVICORN_BIN" main:socketio_app --host 0.0.0.0 --port 8000 --reload > "$LOG_FILE" 2>&1 &

    PID=$!
    echo "$PID" > "$PID_FILE"
    echo "App started with PID $PID. Logs are in $LOG_FILE"
}

stop() {
    echo "Stopping app..."

    # Try to stop via PID file first
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null; then
            kill "$PID"
            echo "Sent SIGTERM to PID $PID"
        fi
        rm "$PID_FILE"
    fi

    # Fallback: Kill any remaining processes matching the pattern
    # This catches reload processes and children
    pkill -f "uvicorn main:app"

    # Wait for processes to exit
    sleep 2

    # Force kill if still running
    if pgrep -f "uvicorn main:app" > /dev/null; then
        echo "Force killing remaining processes..."
        pkill -9 -f "uvicorn main:app"
    fi

    echo "App stopped."
}

restart() {
    stop
    sleep 1
    start
}

case "$1" in
    start) start ;;
    stop) stop ;;
    restart) restart ;;
    *)
        echo "Usage: $0 {start|stop|restart}"
        exit 1
        ;;
esac
exit 0
