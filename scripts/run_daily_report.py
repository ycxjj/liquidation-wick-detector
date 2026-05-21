#!/usr/bin/env python3
"""命令行依次写入日报（与网页「后台生成」一致）。适合 crontab。"""
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daily_scan import _daily_exchange_ids, _git_auto_commit, run_daily_scan  # noqa: E402


def _progress_cb(exchange: str):
    last_t = [0.0]

    def cb(s: str) -> None:
        now = time.time()
        show = now - last_t[0] >= 30
        if "/" in s:
            cur, _, tot = s.partition("/")
            if cur.isdigit() and tot.isdigit():
                c, t = int(cur), int(tot)
                if c % 50 == 0 or c == t:
                    show = True
        if show:
            print(f"  [{exchange}] {s}", flush=True)
            last_t[0] = now

    return cb


def _refresh_wickshield_rates() -> None:
    try:
        from scripts.wickshield.rate_table import refresh_rates_after_daily_reports

        result = refresh_rates_after_daily_reports()
        if result.get("success"):
            print(
                f"wickshield rates: {result.get('symbol_count')} symbols -> data/rates/dynamic_rates.json",
                flush=True,
            )
        elif result.get("skipped"):
            print("wickshield rates: skipped (DISABLE_RATE_TABLE_REFRESH)", flush=True)
        else:
            print(f"wickshield rates: {result.get('error', result)}", flush=True)
    except Exception as e:
        print(f"wickshield rates refresh failed: {e}", flush=True)


if __name__ == "__main__":
    t0 = time.time()
    for ex in _daily_exchange_ids():
        print(f"scanning {ex} ...", flush=True)
        t1 = time.time()
        run_daily_scan(exchange_id=ex, progress_cb=_progress_cb(ex))
        print(f"done {ex} in {time.time() - t1:.0f}s", flush=True)
    print("refreshing wickshield rate table ...", flush=True)
    t2 = time.time()
    _refresh_wickshield_rates()
    print(f"rates done in {time.time() - t2:.0f}s", flush=True)
    report_date = date.today() - timedelta(days=1)
    _git_auto_commit(report_date)
    print("done.")
