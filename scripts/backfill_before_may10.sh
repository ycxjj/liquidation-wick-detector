#!/bin/bash
# 补生成并覆盖 2026-05-10 之前的日报（默认 2026-04-01 ~ 2026-05-09）
# 在服务器上: bash scripts/backfill_before_may10.sh
# 耗时很长，建议 screen/tmux 中运行
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export HOME="${HOME:-/root}"

START="${START:-2026-04-01}"
BEFORE="${BEFORE:-2026-05-10}"
GIT_MODE="${GIT_MODE:-each}"   # none | each | once
DAILY_TIMEFRAME="${DAILY_TIMEFRAME:-1m}"
DAILY_TOP_N="${DAILY_TOP_N:-0}"

echo "补日报: ${START} 起，早于 ${BEFORE}（含 $(date -d "${BEFORE} -1 day" +%Y-%m-%d 2>/dev/null || echo 前一天)）"
echo "配置: DAILY_TIMEFRAME=${DAILY_TIMEFRAME} DAILY_TOP_N=${DAILY_TOP_N}(0=全市场) git=${GIT_MODE}"
echo "按 Ctrl+C 可中断；建议: screen -S backfill"
echo ""

DAILY_TIMEFRAME="$DAILY_TIMEFRAME" DAILY_TOP_N="$DAILY_TOP_N" python3 scripts/backfill_daily_reports.py \
  --before "$BEFORE" \
  --start "$START" \
  --force \
  --git "$GIT_MODE" \
  "$@"
