#!/bin/bash
# 启动 Gunicorn（2GB VPS 默认 2 workers）
set -e
cd "$(dirname "$0")"

export GUNICORN_WORKERS="${GUNICORN_WORKERS:-2}"
export GUNICORN_THREADS="${GUNICORN_THREADS:-2}"

echo "停止旧 gunicorn..."
pkill -9 gunicorn 2>/dev/null || true
sleep 2

if ! python3 -c "import app" 2>/dev/null; then
    echo "❌ 无法 import app，请先修复 Python 错误："
    python3 -c "import app"
    exit 1
fi

if [ -f gunicorn.conf.py ]; then
    echo "使用 gunicorn.conf.py (workers=$GUNICORN_WORKERS)"
    exec gunicorn -c gunicorn.conf.py app:app
else
    echo "未找到 gunicorn.conf.py，使用内联参数 (workers=$GUNICORN_WORKERS)"
    exec gunicorn \
        --bind 0.0.0.0:5000 \
        --workers "$GUNICORN_WORKERS" \
        --threads "$GUNICORN_THREADS" \
        --timeout 600 \
        --graceful-timeout 60 \
        --max-requests 500 \
        --max-requests-jitter 50 \
        app:app
fi
