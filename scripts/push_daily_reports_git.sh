#!/bin/bash
# 手动把日报数据 commit + push（日报已生成但未上 Git 时用）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export HOME="${HOME:-/root}"

DATE="${1:-}"
if [ -z "$DATE" ]; then
  DATE="$(python3 -c "from datetime import date, timedelta; print((date.today()-timedelta(1)).isoformat())")"
fi

echo "==> 检查环境"
python3 scripts/git_auto_commit_check.py || true

echo ""
echo "==> 提交并推送日报: $DATE"
python3 -c "
from datetime import date
import daily_scan as ds
ds._git_auto_commit(date.fromisoformat('${DATE}'))
"

echo ""
echo "==> 最近日志"
tail -15 data/logs/git_auto_commit.log 2>/dev/null || echo "(无 git_auto_commit.log)"

echo ""
echo "==> 远程最新提交（data/reports）"
git log -1 --oneline -- data/reports "data/wick_daily.db" 2>/dev/null || true
