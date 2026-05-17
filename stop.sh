#!/bin/bash
# Wick Detector 停止脚本

echo "停止 Gunicorn 进程..."
pkill -9 gunicorn
sleep 2

# 验证
if ps aux | grep gunicorn | grep -v grep > /dev/null; then
    echo "❌ 进程仍在运行"
    ps aux | grep gunicorn | grep -v grep
else
    echo "✅ 已停止所有 Gunicorn 进程"
fi
