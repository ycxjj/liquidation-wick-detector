#!/usr/bin/env python3
"""
补生成历史日报（指定日期范围内、每个交易所 TopN 插针扫描）。

示例（生成 5 月 10 日之前的所有日报，从 4 月 1 日到 5 月 9 日）:
  python3 scripts/backfill_daily_reports.py --before 2026-05-10 --start 2026-04-01

示例（最近 30 天且早于 5 月 10 日）:
  python3 scripts/backfill_daily_reports.py --before 2026-05-10 --days 30

示例（只补某一天）:
  python3 scripts/backfill_daily_reports.py --date 2026-05-09

示例（生成后每天 push 一次；最后一天再统一 push 用 --git once）:
  python3 scripts/backfill_daily_reports.py --before 2026-05-10 --start 2026-05-01 --git each
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import daily_scan as ds  # noqa: E402


def _parse_date(s: str) -> date:
    return date.fromisoformat(s.strip())


def _date_range(start: date, end: date) -> list[date]:
    if start > end:
        return []
    out: list[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _exchange_list(raw: str | None) -> list[str]:
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    return ds._daily_exchange_ids()


def _day_complete(report_date: date, exchanges: list[str]) -> bool:
    day_dir = os.path.join(ROOT, "data", "reports", report_date.isoformat())
    if not os.path.isdir(day_dir):
        return False
    for ex in exchanges:
        if not os.path.isfile(os.path.join(day_dir, f"{ex}.json")):
            return False
    return True


def _run_one_day(
    report_date: date,
    exchanges: list[str],
    *,
    skip_existing: bool,
    force: bool,
) -> bool:
    if skip_existing and not force and _day_complete(report_date, exchanges):
        print(f"  [skip] {report_date} 已有全部 {len(exchanges)} 所 JSON", flush=True)
        return True

    ok_any = False
    for ex in exchanges:
        print(f"  [scan] {report_date} {ex}", flush=True)
        try:
            ds.run_daily_scan(report_date=report_date, exchange_id=ex)
            ok_any = True
        except Exception:
            print(f"  [FAIL] {report_date} {ex}:", flush=True)
            traceback.print_exc()
    return ok_any


def main() -> int:
    parser = argparse.ArgumentParser(description="补生成历史日报")
    parser.add_argument(
        "--before",
        type=_parse_date,
        help="只生成严格早于该日的日报（如 2026-05-10 表示最晚到 2026-05-09）",
    )
    parser.add_argument("--start", type=_parse_date, help="起始日期（含）")
    parser.add_argument("--end", type=_parse_date, help="结束日期（含）；默认由 --before 推导")
    parser.add_argument("--date", type=_parse_date, help="只生成单日")
    parser.add_argument(
        "--days",
        type=int,
        help="与 --before 联用：从 (before-1天) 往前共 N 天",
    )
    parser.add_argument(
        "--exchanges",
        help="逗号分隔交易所，默认 DAILY_EXCHANGES 全部",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="若当日各所 JSON 已齐则跳过（默认开启）",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_false",
        dest="skip_existing",
        help="不跳过已有目录",
    )
    parser.add_argument("--force", action="store_true", help="即使已有 JSON 也重新扫描")
    parser.add_argument(
        "--git",
        choices=("none", "each", "once"),
        default="none",
        help="none=不提交; each=每天提交推送; once=全部完成后只推送最后一天",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印将处理的日期")
    args = parser.parse_args()

    exchanges = _exchange_list(args.exchanges)
    if not exchanges:
        print("错误: 交易所列表为空")
        return 1

    ds._ensure_db()

    if args.date:
        dates = [args.date]
    else:
        if args.before is None and args.end is None:
            print("错误: 请指定 --date、或 --before、或 --start/--end")
            return 1
        end = args.end
        if end is None:
            if args.before is None:
                print("错误: 需要 --before 或 --end")
                return 1
            end = args.before - timedelta(days=1)
        start = args.start
        if start is None:
            if args.days:
                start = end - timedelta(days=args.days - 1)
            else:
                print("错误: 请指定 --start 或 --days")
                return 1
        if args.before and end >= args.before:
            end = args.before - timedelta(days=1)
        dates = _date_range(start, end)

    if not dates:
        print("没有需要处理的日期")
        return 0

    print(f"将处理 {len(dates)} 天: {dates[0]} .. {dates[-1]}")
    print(f"交易所 ({len(exchanges)}): {', '.join(exchanges)}")
    print(f"git 模式: {args.git}")
    if args.dry_run:
        for d in dates:
            tag = "skip?" if _day_complete(d, exchanges) else "run"
            print(f"  {d} [{tag}]")
        return 0

    failed: list[date] = []
    for i, d in enumerate(dates, 1):
        print(f"\n=== [{i}/{len(dates)}] {d} ===", flush=True)
        if not _run_one_day(
            d, exchanges, skip_existing=args.skip_existing, force=args.force
        ):
            failed.append(d)
            continue
        if args.git == "each":
            print(f"  [git] push {d}", flush=True)
            ds._git_auto_commit(d)

    if args.git == "once" and dates:
        last_ok = dates[-1] if not failed else max(set(dates) - set(failed), default=None)
        if last_ok:
            print(f"\n[git] 批量完成后推送 {last_ok}", flush=True)
            ds._git_auto_commit(last_ok)

    print("\n完成.")
    if failed:
        print("失败日期:", ", ".join(x.isoformat() for x in failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
