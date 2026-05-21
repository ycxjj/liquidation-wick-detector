#!/usr/bin/env python3
"""服务器诊断：投保 API 依赖与 LAB 报价试算。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    print("== modules ==")
    for name in (
        "scripts.wickshield.policies_db",
        "scripts.wickshield.policy_service",
        "scripts.wickshield.chain_payout",
        "scripts.wickshield._json_util",
    ):
        try:
            __import__(name)
            print(name, "OK")
        except Exception as e:
            print(name, "FAIL", e)
            return 1

    print("== quote LAB/USDT 90U ==")
    from scripts.wickshield.policy_service import quote_policy

    out = quote_policy(
        symbol="LAB/USDT",
        coverage_amount=90,
        days=7,
        leverage=19,
        product_tier="basic",
    )
    print(json.dumps(out, ensure_ascii=False, indent=2)[:2000])
    if not out.get("success"):
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
