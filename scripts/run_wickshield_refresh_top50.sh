#!/usr/bin/env bash
# 从六所拉取最新热门 USDT 永续 Top50，写入 data/wickshield/monitor_symbols.json
# 用法: bash scripts/run_wickshield_refresh_top50.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p data/wickshield data/logs
echo "拉取六所热门币（约 1-3 分钟）..."
python3 -m scripts.wickshield.cli symbols refresh --limit 50 --workers 4 --compact | tee -a data/logs/wickshield_symbols_refresh.log
echo ""
echo "完成。请确认未在 .env 覆盖 WICKSHIELD_MONITOR_SYMBOLS，然后:"
echo "  sudo systemctl restart wick-detector"
