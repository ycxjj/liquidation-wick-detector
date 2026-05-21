#!/bin/bash
# 部署首页 landing.html（带按钮副标题）
set -euo pipefail
ROOT="${WICK_DETECTOR_ROOT:-/root/liquidation-wick-detector}"
cd "$ROOT"

echo "==> 拉取代码"
git pull --ff-only

echo "==> 校验 landing.html"
grep -q 'cta-sub' templates/landing.html
grep -q '检测当前持仓币种' templates/landing.html

echo "==> 重启服务（Flask 重新读模板）"
if systemctl is-active --quiet wick-detector 2>/dev/null; then
  systemctl restart wick-detector
else
  echo "请手动重启 gunicorn"
fi

sleep 2
if curl -fsS http://127.0.0.1:5000/ | grep -q 'cta-sub'; then
  echo "OK: 本机首页已含副标题"
else
  echo "WARN: 本机 HTML 仍无 cta-sub，请确认路径与分支" >&2
  exit 1
fi
echo "公网请 Ctrl+F5 或 Cloudflare 清缓存后打开 https://wickdetector.com/"
