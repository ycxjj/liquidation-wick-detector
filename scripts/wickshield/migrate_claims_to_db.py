"""将 claims_log.jsonl 历史记录导入 SQLite（一次性运维）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.wickshield.claims_db import CLAIMS_DB_PATH, insert_claims_batch, init_db  # noqa: E402

_LOG = _ROOT / "data" / "wickshield" / "claims_log.jsonl"


def main() -> int:
    if not _LOG.is_file():
        print("无 claims_log.jsonl，跳过")
        return 0
    entries = []
    for line in _LOG.read_text(encoding="utf-8").strip().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not entries:
        print("日志为空")
        return 0
    init_db()
    n = insert_claims_batch(entries)
    print(f"已导入 {n} 条 → {CLAIMS_DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
