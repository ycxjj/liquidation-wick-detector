#!/bin/bash
# 历史日报补跑：币安/欧易/Bybit/MEXC/Bitget 用 1m；Gate 用 5m（避免 1m 超窗口与限频）
#
# 默认从 2026-05-09 往前补到 2026-04-01（含首尾）。日期范围：START(早) .. END(晚，默认 5月9日)
#
# 前台执行：
#   bash scripts/backfill_history_gate5m_others1m.sh
#
# 后台执行：
#   BACKGROUND=1 bash scripts/backfill_history_gate5m_others1m.sh
#
# 自定义范围（例如补到 3 月 1 日）：
#   START=2026-03-01 END=2026-05-09 BACKGROUND=1 bash scripts/backfill_history_gate5m_others1m.sh
#
# 不推 Git：
#   GIT_MODE=none bash scripts/backfill_history_gate5m_others1m.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export HOME="${HOME:-/root}"

START="${START:-2026-04-01}"
END="${END:-2026-05-09}"
GIT_MODE="${GIT_MODE:-each}"
DAILY_TOP_N="${DAILY_TOP_N:-0}"
DAILY_SCAN_WORKERS="${DAILY_SCAN_WORKERS:-1}"
BACKGROUND="${BACKGROUND:-0}"
OTHERS_EX="${OTHERS_EX:-binanceusdm,okx,bybit,mexc,bitget}"

LOG_DIR="data/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_FILE:-$LOG_DIR/backfill_gate5m_others1m_${START}_${END}_$(date +%Y%m%d_%H%M%S).log}"

run_backfill() {
  local tf="$1"
  local exchanges="$2"
  local label="$3"
  echo ""
  echo "============================================================"
  echo "[$label] timeframe=$tf exchanges=$exchanges"
  echo "日期: $START .. $END"
  echo "============================================================"
  DAILY_TIMEFRAME="$tf" \
  DAILY_TOP_N="$DAILY_TOP_N" \
  DAILY_SCAN_WORKERS="$DAILY_SCAN_WORKERS" \
  python3 scripts/backfill_daily_reports.py \
    --force \
    --git "$GIT_MODE" \
    --start "$START" \
    --end "$END" \
    --exchanges "$exchanges"
}

_run_all() {
  echo "历史日报补跑开始: $(date -Iseconds)"
  echo "START=$START END=$END (从 $END 往前到 $START)"
  echo "TOP_N=$DAILY_TOP_N (0=全市场) WORKERS=$DAILY_SCAN_WORKERS GIT=$GIT_MODE"
  echo "日志: $LOG_FILE"

  # 五所若有失败日会 exit 1；勿用 set -e 中断，否则 Gate 阶段永远跑不到
  run_backfill "1m" "$OTHERS_EX" "五所 1m" || echo "[warn] 五所 1m 部分日期失败，继续 Gate 5m…"
  run_backfill "5m" "gate" "Gate 5m"

  echo ""
  echo "全部完成: $(date -Iseconds)"
}

if [ "$BACKGROUND" = "1" ]; then
  echo "后台模式，日志: $LOG_FILE"
  BACKGROUND=0 nohup bash "$0" --foreground >> "$LOG_FILE" 2>&1 &
  echo "已启动 PID=$!"
  echo "查看: tail -f $LOG_FILE"
  exit 0
fi

if [ "${1:-}" = "--foreground" ]; then
  _run_all
  exit 0
fi

# 前台：同时输出到终端和日志
_run_all 2>&1 | tee -a "$LOG_FILE"
