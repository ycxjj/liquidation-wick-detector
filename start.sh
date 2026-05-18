#!/bin/bash
# Wick Detector 启动脚本

cd /root/liquidation-wick-detector

# 1. 停止旧进程
echo "停止旧进程..."
pkill -9 gunicorn
sleep 3

# 2. 启动 Gunicorn
echo "启动 Gunicorn..."
# 2GB 内存 VPS：默认 2 workers；仍紧张可 export GUNICORN_WORKERS=1
if [ -f gunicorn.conf.py ]; then
    nohup gunicorn -c gunicorn.conf.py app:app >> gunicorn.log 2>&1 &
else
    nohup gunicorn --bind 0.0.0.0:5000 --workers "${GUNICORN_WORKERS:-2}" --threads 2 \
        --timeout 600 --graceful-timeout 60 --max-requests 500 app:app >> gunicorn.log 2>&1 &
fi

# 3. 等待启动
sleep 2

# 4. 验证
echo "验证进程..."
if ps aux | grep gunicorn | grep -v grep > /dev/null; then
    echo "✅ Gunicorn 启动成功"
    echo ""
    echo "进程信息:"
    ps aux | grep gunicorn | grep -v grep
    echo ""
    echo "访问地址: http://your-server-ip:5000"
else
    echo "❌ Gunicorn 启动失败"
    echo "查看日志: tail -f gunicorn.log"
    exit 1
fi
