#!/bin/bash
set -eu
# shellcheck source=_wickshield_env.sh
source "$(dirname "$0")/_wickshield_env.sh"
export DAILY_REPORT_TZ="${DAILY_REPORT_TZ:-Asia/Shanghai}"
export WICKSHIELD_POOL="${WICKSHIELD_POOL:-50000}"
export WICKSHIELD_COVERAGE="${WICKSHIELD_COVERAGE:-100000}"
mkdir -p data/logs data/wickshield
_wickshield_cli reset
