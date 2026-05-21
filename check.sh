#!/bin/bash
# 诊断和修复脚本

echo "=========================================="
echo "  Wick Detector 诊断工具"
echo "=========================================="
echo ""

# 1. 检查 Gunicorn 进程
echo "[1/5] 检查 Gunicorn 进程..."
if ps aux | grep gunicorn | grep -v grep > /dev/null; then
    echo "✅ Gunicorn 进程正在运行"
    ps aux | grep gunicorn | grep -v grep
else
    echo "❌ Gunicorn 进程未运行"
fi
echo ""

# 2. 检查端口占用
echo "[2/5] 检查端口 5000..."
if netstat -tulpn 2>/dev/null | grep :5000 > /dev/null; then
    echo "✅ 端口 5000 正在监听"
    netstat -tulpn 2>/dev/null | grep :5000
else
    echo "❌ 端口 5000 未监听"
fi
echo ""

# 3. 检查日志文件
echo "[3/5] 检查日志文件..."
if [ -f "gunicorn.log" ]; then
    echo "最近的错误日志："
    tail -20 gunicorn.log | grep -i error || echo "无错误信息"
else
    echo "❌ 日志文件不存在"
fi
echo ""

# 4. 检查 Python 依赖
echo "[4/5] 检查 Python 依赖..."
python3 -c "import flask, ccxt, pandas, numpy, requests, apscheduler" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✅ Python 依赖已安装"
else
    echo "❌ Python 依赖缺失"
    echo "运行: pip3 install -r requirements.txt"
fi
echo ""

# 5. 检查数据库
echo "[5/5] 检查数据库..."
if [ -d "data" ]; then
    echo "✅ data 目录存在"
    ls -lh data/*.db 2>/dev/null || echo "⚠️  数据库文件不存在（首次运行会自动创建）"
else
    echo "⚠️  data 目录不存在，将自动创建"
    mkdir -p data
fi
echo ""

# 诊断结果
echo "=========================================="
echo "  诊断完成"
echo "=========================================="
echo ""

# 询问是否重启
read -p "是否尝试重启服务？(y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "正在重启服务..."
    ./stop.sh
    sleep 2
    ./start.sh
fi
