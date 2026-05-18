#!/bin/bash
# 紧急修复脚本 - 解决重复函数定义问题

echo "=========================================="
echo "  紧急修复 - 删除重复函数定义"
echo "=========================================="
echo ""

cd /root/liquidation-wick-detector

# 1. 备份当前文件
echo "[1/4] 备份当前文件..."
cp app.py app.py.backup.$(date +%Y%m%d_%H%M%S)
echo "✅ 已备份"
echo ""

# 2. 停止服务
echo "[2/4] 停止服务..."
pkill -9 gunicorn
sleep 3
echo "✅ 已停止"
echo ""

# 3. 上传新文件提示
echo "[3/4] 请确保已上传最新的 app.py"
echo "     scp app.py root@server:/root/liquidation-wick-detector/"
echo ""
read -p "已上传最新文件？(y/n) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "请先上传文件后再运行此脚本"
    exit 1
fi

# 4. 启动服务
echo "[4/4] 启动服务..."
nohup gunicorn -c gunicorn.conf.py app:app > gunicorn.log 2>&1 &
sleep 3

# 验证
if ps aux | grep gunicorn | grep -v grep > /dev/null; then
    echo "✅ 服务启动成功！"
    echo ""
    ps aux | grep gunicorn | grep -v grep
    echo ""
    echo "访问地址: http://wickdetector.com"
else
    echo "❌ 服务启动失败，查看日志:"
    tail -20 gunicorn.log
    exit 1
fi
