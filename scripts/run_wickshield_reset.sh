#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export DAILY_REPORT_TZ="${DAILY_REPORT_TZ:-Asia/Shanghai}"
export WICKSHIELD_POOL="${WICKSHIELD_POOL:-50000}"
export WICKSHIELD_COVERAGE="${WICKSHIELD_COVERAGE:-100000}"
mkdir -p data/logs data/wickshield
python3 -m scripts.wickshield.cli reset --compact
