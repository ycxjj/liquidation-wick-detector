#!/usr/bin/env bash
# WickShield 理赔监控 — 建议 crontab 每 5 分钟
# */5 * * * * cd /root/liquidation-wick-detector && bash scripts/run_wickshield_monitor.sh >> data/logs/wickshield_monitor.log 2>&1
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export WICKSHIELD_POOL="${WICKSHIELD_POOL:-50000}"
export WICKSHIELD_COVERAGE="${WICKSHIELD_COVERAGE:-100000}"
# v2.0：并行拉取、超时熔断、限频、赔付前全量复核、结果缓存
export WICKSHIELD_MONITOR_DAYS_BACK="${WICKSHIELD_MONITOR_DAYS_BACK:-0.3}"
export WICKSHIELD_MONITOR_LIGHT="${WICKSHIELD_MONITOR_LIGHT:-1}"
export WICKSHIELD_REQUEST_TIMEOUT="${WICKSHIELD_REQUEST_TIMEOUT:-3}"
export WICKSHIELD_SKIP_TIMEOUT="${WICKSHIELD_SKIP_TIMEOUT:-1}"
export WICKSHIELD_CIRCUIT_BREAKER="${WICKSHIELD_CIRCUIT_BREAKER:-1}"
export WICKSHIELD_RATE_LIMIT_ENABLE="${WICKSHIELD_RATE_LIMIT_ENABLE:-1}"
export WICKSHIELD_CLAIM_FULL_VERIFY="${WICKSHIELD_CLAIM_FULL_VERIFY:-1}"
export WICKSHIELD_CACHE_TTL="${WICKSHIELD_CACHE_TTL:-30}"
# Worker 未显式设置时由 fetch_guard.dynamic_* 按小时自动选择 4/6/8
export WICKSHIELD_OHLCV_WORKERS="${WICKSHIELD_OHLCV_WORKERS:-}"
export WICKSHIELD_MONITOR_WORKERS="${WICKSHIELD_MONITOR_WORKERS:-}"
# Redis（可选）：export WICKSHIELD_REDIS_HOST=127.0.0.1
mkdir -p data/logs data/wickshield
python3 -m scripts.wickshield.cli monitor --compact
