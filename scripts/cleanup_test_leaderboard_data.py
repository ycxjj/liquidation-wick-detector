#!/usr/bin/env python3
"""清理周榜测试数据，或清空全部周榜快照与奖励（生产误操作恢复）。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import points_system as ps  # noqa: E402


def _preview_all_weekly() -> None:
    with ps._connect() as conn:
        snaps = conn.execute(
            "SELECT id, week_start, week_end, created_by FROM weekly_snapshots ORDER BY id"
        ).fetchall()
        rewards = conn.execute("SELECT COUNT(*) FROM reward_distributions").fetchone()[0]
    print(f"周榜快照共 {len(snaps)} 条，奖励记录共 {rewards} 条")
    for sid, ws, we, by in snaps:
        print(f"  id={sid}  {ws} ~ {we}  by={by}")


def _preview_test_only() -> tuple[list, list]:
    with ps._connect() as conn:
        users = conn.execute(
            """
            SELECT wallet_address FROM users
            WHERE wallet_address LIKE '0xfunctionaltest%'
               OR wallet_address LIKE '0xhealthcheck%'
            """
        ).fetchall()
        snaps = conn.execute(
            """
            SELECT id, week_start, week_end, created_by FROM weekly_snapshots
            WHERE created_by = 'health_check' OR week_start >= '2099-01-01'
            """
        ).fetchall()
    return users, snaps


def main() -> int:
    ps._ensure_db()
    all_weekly = os.environ.get("ALL_WEEKLY") == "yes"

    if all_weekly:
        print("模式: 清空全部周榜快照与奖励")
        _preview_all_weekly()
        if os.environ.get("CONFIRM") != "yes":
            print("\n确认清空请执行:")
            print("  ALL_WEEKLY=yes CONFIRM=yes python3 scripts/cleanup_test_leaderboard_data.py")
            return 1
        stats = ps.cleanup_all_weekly_rewards_data()
        print("清空完成:", stats)
        return 0

    users, snaps = _preview_test_only()
    if not users and not snaps:
        print("未发现测试账号或测试周榜快照，无需清理。")
        return 0

    if users:
        print("测试账号:")
        for (w,) in users:
            print(" ", w)
    if snaps:
        print("测试周榜快照:")
        for sid, ws, we, by in snaps:
            print(f"  id={sid}  {ws} ~ {we}  by={by}")

    if os.environ.get("CONFIRM") != "yes":
        print("\n仅清理测试数据:")
        print("  CONFIRM=yes python3 scripts/cleanup_test_leaderboard_data.py")
        print("\n清空全部周榜（含刚创建的快照）:")
        print("  ALL_WEEKLY=yes CONFIRM=yes python3 scripts/cleanup_test_leaderboard_data.py")
        return 1

    stats = ps.cleanup_automation_test_data()
    print("清理完成:", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
