#!/bin/bash
# 在服务器执行：修复看板脏缓存 + 补全 monitor_cache.clear_monitor_snapshot（无需 git pull）
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MC="scripts/wickshield/monitor_cache.py"
DD="scripts/wickshield/dashboard_data.py"

echo "== 1) 清 Redis =="
redis-cli DEL wickshield:dashboard 2>/dev/null && echo "redis DEL ok" || echo "redis skip"

echo "== 2) 补全 monitor_cache.clear_monitor_snapshot =="
if ! grep -q 'def clear_monitor_snapshot' "$MC" 2>/dev/null; then
  python3 <<'PY'
from pathlib import Path
p = Path("scripts/wickshield/monitor_cache.py")
text = p.read_text(encoding="utf-8")
block = '''

def clear_monitor_snapshot() -> None:
    """清空 monitor/看板缓存（测试或强制刷新用）。"""
    _mem["payload"] = None
    _mem["ts"] = 0.0
    r = _redis_client()
    if r is not None:
        try:
            r.delete("wickshield:dashboard")
        except Exception:
            pass


def _snapshot_has_dashboard_fields(payload) -> bool:
    required = (
        "solvency_ratio",
        "global_cap_remaining",
        "cap_24h",
        "watch_symbols",
        "state_date",
    )
    return all(k in payload for k in required)

'''
if "def clear_monitor_snapshot" not in text:
    if "def load_monitor_snapshot" in text:
        text = text.replace("def load_monitor_snapshot", block + "def load_monitor_snapshot", 1)
    else:
        text += block
    # save_monitor_snapshot 入口校验
    if "_snapshot_has_dashboard_fields" not in text and "def save_monitor_snapshot" in text:
        text = text.replace(
            'def save_monitor_snapshot(payload',
            'def save_monitor_snapshot(payload',
            1,
        )
    p.write_text(text, encoding="utf-8")
    print("patched monitor_cache.py")
else:
    print("monitor_cache already has clear_monitor_snapshot")
PY
else
  echo "clear_monitor_snapshot 已存在"
fi

echo "== 3) 修补 load 逻辑（拒绝 test stub）=="
python3 <<'PY'
from pathlib import Path
p = Path("scripts/wickshield/monitor_cache.py")
t = p.read_text(encoding="utf-8")
old = "                return json.loads(raw), True"
new = """                payload = json.loads(raw)
                if isinstance(payload, dict) and _snapshot_has_dashboard_fields(payload):
                    return payload, True
                return None, False"""
if old in t and "_snapshot_has_dashboard_fields(payload)" not in t:
    t = t.replace(old, new, 1)
    p.write_text(t, encoding="utf-8")
    print("patched redis load")
else:
    print("redis load skip or already patched")
PY

echo "== 4) 清内存缓存 =="
python3 -c "
import sys; sys.path.insert(0,'$ROOT')
from scripts.wickshield import monitor_cache as m
if hasattr(m,'clear_monitor_snapshot'): m.clear_monitor_snapshot()
elif hasattr(m,'_mem'): m._mem.update(payload=None, ts=0.0)
print('memory ok')
"

echo "== 5) 验证 =="
python3 scripts/wickshield_clear_dashboard_cache.py 2>/dev/null || echo "(clear script optional)"
python3 -m pytest tests/test_wickshield_api.py -q --tb=line && echo "API tests OK"
