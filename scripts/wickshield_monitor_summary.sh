#!/bin/bash
# 监控一行摘要（stdout 仅 JSON 最后一行，日志走 stderr）
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export WICKSHIELD_MONITOR_SKIP_DASHBOARD_CACHE="${WICKSHIELD_MONITOR_SKIP_DASHBOARD_CACHE:-1}"
json="$(bash scripts/run_wickshield_dynamic_monitor.sh 2>/dev/null | tail -1)"
python3 -c "
import json, sys
d = json.loads(sys.argv[1])
m = d.get('optimization_metrics') or {}
print('ok', d.get('duration_ms'), 'ms | symbols', d.get('symbols_checked'),
      '| timeouts', m.get('timeout_count'), '| payout_today', d.get('global_payout_today'))
for r in (d.get('results') or [])[:8]:
    mk = r.get('market') or {}
    print(' ', r.get('symbol'), r.get('decision'),
          'amp', mk.get('max_amplitude_1h_percent'), 'thr', r.get('threshold_percent'))
n = len(d.get('results') or [])
if n > 8:
    print(' ... +', n - 8, 'more')
" "$json"
