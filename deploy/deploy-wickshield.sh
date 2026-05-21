#!/bin/bash
# 在 Vultr 服务器上执行：部署 WickShield 看板路由并重启 Gunicorn
set -euo pipefail
ROOT="${WICK_DETECTOR_ROOT:-/root/liquidation-wick-detector}"
cd "$ROOT"

echo "==> 拉取最新代码"
git pull --ff-only

echo "==> 校验 WickShield 文件"
test -f templates/wickshield_dashboard.html
grep -q 'wickshield_dashboard_page' app.py
test -d scripts/wickshield

echo "==> 重启 wick-detector"
if systemctl is-active --quiet wick-detector 2>/dev/null; then
  systemctl restart wick-detector
  sleep 2
  systemctl is-active wick-detector
else
  echo "未找到 systemd 单元，尝试 bash start-gunicorn.sh"
  bash start-gunicorn.sh restart || true
fi

echo "==> 本机探测"
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000/wickshield || echo "000")
api=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000/api/wickshield/dashboard || echo "000")
echo "/wickshield -> HTTP $code"
echo "/api/wickshield/dashboard -> HTTP $api"
if [ "$code" != "200" ]; then
  echo "失败: 请确认 app.py 含 /wickshield 且 Gunicorn 已重启" >&2
  exit 1
fi
echo "OK — 公网访问 https://wickdetector.com/wickshield"
