#!/bin/bash
# 快速修复脚本 - 解决 502 错误

echo "=========================================="
echo "  快速修复 502 错误"
echo "=========================================="
echo ""

cd /root/liquidation-wick-detector

# 1. 停止所有 Gunicorn 进程
echo "[1/4] 停止旧进程..."
pkill -9 gunicorn
pkill -9 python
sleep 3
echo "✅ 已停止所有进程"
echo ""

# 2. 检查并安装依赖
echo "[2/4] 检查依赖..."
if ! python3 -c "import flask" 2>/dev/null; then
    echo "安装依赖..."
    pip3 install -r requirements.txt
    pip3 install gunicorn
fi
echo "✅ 依赖检查完成"
echo ""

# 3. 创建必要的目录
echo "[3/4] 创建目录..."
mkdir -p data logs
chmod 755 data
echo "✅ 目录创建完成"
echo ""

# 4. 启动服务
echo "[4/4] 启动服务..."
nohup gunicorn -c gunicorn.conf.py app:app > gunicorn.log 2>&1 &
sleep 3

# 验证
if ps aux | grep gunicorn | grep -v grep > /dev/null; then
    echo "✅ 服务启动成功！"
    echo ""
    echo "进程信息:"
    ps aux | grep gunicorn | grep -v grep
    echo ""
    echo "访问地址: http://wickdetector.com"
    echo "查看日志: tail -f gunicorn.log"
else
    echo "❌ 服务启动失败"
    echo ""
    echo "查看错误日志:"
    tail -20 gunicorn.log
    exit 1
fi
