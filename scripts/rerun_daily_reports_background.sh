#!/bin/bash
# 后台重跑日报并覆盖旧数据（默认强制使用 1m K线 + 全市场扫描）。
#
# 示例：
#   START=2026-05-01 BEFORE=2026-05-10 bash scripts/rerun_daily_reports_background.sh
#   DATE=2026-05-09 bash scripts/rerun_daily_reports_background.sh
#   START=2026-05-01 END=2026-05-17 GIT_MODE=each bash scripts/rerun_daily_reports_background.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p data/logs

DATE="${DATE:-}"
START="${START:-}"
END="${END:-}"
BEFORE="${BEFORE:-}"
DAYS="${DAYS:-}"
GIT_MODE="${GIT_MODE:-each}" # none | each | once
DAILY_TIMEFRAME="${DAILY_TIMEFRAME:-1m}"
DAILY_TOP_N="${DAILY_TOP_N:-0}"
LOG_FILE="${LOG_FILE:-data/logs/rerun_daily_reports_$(date +%Y%m%d_%H%M%S).log}"

ARGS=(--force --git "$GIT_MODE")
if [ -n "$DATE" ]; then
  ARGS+=(--date "$DATE")
else
  if [ -n "$BEFORE" ]; then
    ARGS+=(--before "$BEFORE")
  fi
  if [ -n "$START" ]; then
    ARGS+=(--start "$START")
  fi
  if [ -n "$END" ]; then
    ARGS+=(--end "$END")
  fi
  if [ -n "$DAYS" ]; then
    ARGS+=(--days "$DAYS")
  fi
fi

if [ ${#ARGS[@]} -le 3 ]; then
  echo "请指定 DATE，或 START+END，或 START+BEFORE，或 BEFORE+DAYS"
  echo "例: START=2026-05-01 BEFORE=2026-05-10 bash scripts/rerun_daily_reports_background.sh"
  exit 1
fi

echo "后台重跑日报，日志: $LOG_FILE"
echo "配置: DAILY_TIMEFRAME=${DAILY_TIMEFRAME} DAILY_TOP_N=${DAILY_TOP_N}(0=全市场) GIT_MODE=$GIT_MODE"
echo "参数: ${ARGS[*]} $*"

nohup env \
  HOME="${HOME:-/root}" \
  DAILY_TIMEFRAME="$DAILY_TIMEFRAME" \
  DAILY_TOP_N="$DAILY_TOP_N" \
  python3 scripts/backfill_daily_reports.py "${ARGS[@]}" "$@" \
  > "$LOG_FILE" 2>&1 &

PID=$!
echo "已启动 PID=$PID"
echo "查看日志: tail -f $LOG_FILE"
