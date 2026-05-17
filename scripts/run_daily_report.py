#!/usr/bin/env python3
"""命令行依次写入日报（与网页「后台生成」一致）。适合 crontab。"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daily_scan import _daily_exchange_ids, _git_auto_commit, run_daily_scan  # noqa: E402


if __name__ == "__main__":
    for ex in _daily_exchange_ids():
        print("scanning", ex, flush=True)
        run_daily_scan(exchange_id=ex)
    report_date = date.today() - timedelta(days=1)
    _git_auto_commit(report_date)
    print("done.")
