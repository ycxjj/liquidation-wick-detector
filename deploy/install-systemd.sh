#!/bin/bash
# 安装并启用 wick-detector systemd 服务（在服务器上以 root 执行）
set -e

SERVICE_NAME="wick-detector"
PROJECT_DIR="/root/liquidation-wick-detector"
UNIT_SRC="${PROJECT_DIR}/deploy/wick-detector.service"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"

if [ "$(id -u)" -ne 0 ]; then
    echo "请使用 root 运行: sudo bash deploy/install-systemd.sh"
    exit 1
fi

if [ ! -d "$PROJECT_DIR" ]; then
    echo "未找到项目目录: $PROJECT_DIR"
    exit 1
fi

if [ ! -f "$PROJECT_DIR/gunicorn.conf.py" ]; then
    echo "缺少 gunicorn.conf.py，请先 git pull 或按文档创建该文件"
    exit 1
fi

if ! python3 -c "import gunicorn" 2>/dev/null; then
    echo "安装 gunicorn..."
    pip3 install gunicorn
fi

echo "==> 安装 unit: $UNIT_DST"
cp "$UNIT_SRC" "$UNIT_DST"

echo "==> 停止手动 nohup / 旧 gunicorn 进程"
pkill -9 gunicorn 2>/dev/null || true
sleep 2

# 若存在旧名称的服务，尝试停用（忽略错误）
systemctl disable gunicorn 2>/dev/null || true
systemctl stop gunicorn 2>/dev/null || true

echo "==> 重载 systemd"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

sleep 2
echo ""
echo "==> 状态"
systemctl status "$SERVICE_NAME" --no-pager -l || true

echo ""
echo "==> 进程与内存"
ps aux --sort=-%mem | grep gunicorn | grep -v grep || echo "(无 gunicorn 进程)"
free -h

echo ""
echo "==> 本机探活"
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:5000/ || echo "curl 失败，请检查: journalctl -u $SERVICE_NAME -n 50 --no-pager"

echo ""
echo "完成。常用命令:"
echo "  systemctl status $SERVICE_NAME"
echo "  systemctl restart $SERVICE_NAME"
echo "  journalctl -u $SERVICE_NAME -f"
