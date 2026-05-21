#!/usr/bin/env python3
"""WickShield 全量测试（避免 .sh 在 Windows 上 CRLF 导致 Linux 无法执行）。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    os.chdir(ROOT)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["WICKSHIELD_MONITOR_SKIP_DASHBOARD_CACHE"] = "1"

    clear = ROOT / "scripts" / "wickshield_clear_dashboard_cache.py"
    if clear.is_file():
        subprocess.run([sys.executable, str(clear)], env=env, check=False)

    tests = [
        "tests/test_wickshield.py",
        "tests/test_claims_db.py",
        "tests/test_wickshield_flow_matrix.py",
        "tests/test_wickshield_api.py",
    ]
    policies = ROOT / "tests" / "test_wickshield_policies.py"
    if policies.is_file():
        tests.append(str(policies))

    cmd = [sys.executable, "-m", "pytest", *tests, "-v", "--tb=short", *sys.argv[1:]]
    return subprocess.call(cmd, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
