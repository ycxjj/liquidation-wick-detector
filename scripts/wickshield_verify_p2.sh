#!/bin/bash
# P2 部署后快速验证：理赔 DB + 监控 JSON 输出
set -eu
source "$(dirname "$0")/_wickshield_env.sh"

export WICKSHIELD_MONITOR_SKIP_DASHBOARD_CACHE="${WICKSHIELD_MONITOR_SKIP_DASHBOARD_CACHE:-1}"
export WICKSHIELD_CLAIMS_DB="${WICKSHIELD_CLAIMS_DB:-1}"
export WICKSHIELD_OHLCV_CACHE="${WICKSHIELD_OHLCV_CACHE:-1}"

OUT="${ROOT}/data/logs/wickshield_p2_verify.json"
ERR="${ROOT}/data/logs/wickshield_p2_verify.err"
mkdir -p "${ROOT}/data/logs"

echo "== claims.db =="
if [ -f "${ROOT}/data/wickshield/claims.db" ]; then
  python3 -c "
import sqlite3
c=sqlite3.connect('${ROOT}/data/wickshield/claims.db')
n=c.execute('select count(*) from wickshield_claims').fetchone()[0]
print('rows', n)
"
else
  echo "missing claims.db"
  exit 1
fi

echo "== monitor (compact, stderr -> ${ERR}) =="
if python3 -m scripts.wickshield.cli monitor --compact >"$OUT" 2>"$ERR"; then
  :
else
  echo "monitor exit code: $?" >&2
  tail -20 "$ERR" >&2 || true
  exit 1
fi

python3 -c "
import json, sys
raw=open('${OUT}', encoding='utf-8').read().strip()
if not raw:
    print('empty stdout; see ${ERR}', file=sys.stderr)
    sys.exit(1)
d=json.loads(raw)
m=d.get('optimization_metrics') or {}
print('ok duration_ms=', d.get('duration_ms'))
print('symbols_checked=', d.get('symbols_checked'))
print('live_cache_hit_count=', m.get('live_cache_hit_count'))
print('claims_db_batch=', d.get('optimization_metrics', {}).get('claims_db_batch', d.get('claims_db_batch')))
print('ohlcv_live_cache=', m.get('ohlcv_live_cache'))
"
