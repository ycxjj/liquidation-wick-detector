#!/usr/bin/env python3
"""清理积分系统测试/垃圾钱包地址。

识别规则（满足任一即视为测试账号）:
  - metadata.is_test_user 或 test_fingerprint
  - 地址含 test（如 0xtest999、test_xxx）
  - 0x 开头但长度不是 42 或非法十六进制（短地址垃圾数据）

用法:
  python clean_random_addresses.py              # 预览后确认删除测试账号
  python clean_random_addresses.py --yes        # 直接删除测试账号
  python clean_random_addresses.py --dry-run    # 只列出，不删除
  python clean_random_addresses.py --all      # 清空全部用户（含真实钱包）
  python clean_random_addresses.py --all --yes
"""

import argparse
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import points_system

DB_PATH = points_system.DB_PATH
_VALID_ETH = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _parse_metadata(metadata_text):
    try:
        return json.loads(metadata_text or "{}")
    except Exception:
        return {}


def test_wallet_reason(wallet_address: str, metadata_text) -> str:
    """返回测试账号判定原因；非测试账号返回空字符串。"""
    addr = (wallet_address or "").strip()
    meta = _parse_metadata(metadata_text)

    if meta.get("is_test_user"):
        return "metadata.is_test_user"
    if meta.get("test_fingerprint"):
        return "metadata.test_fingerprint"

    lower = addr.lower()
    if "test" in lower:
        return "地址包含 test"
    if lower.startswith("test_"):
        return "test_ 前缀"

    if addr.startswith("0x"):
        if len(addr) != 42:
            return f"0x 地址长度异常({len(addr)})"
        if not _VALID_ETH.match(addr):
            return "0x 地址非法十六进制"
    elif not addr.startswith("T"):
        # 非 TRC20、非标准 0x，且较短 → 多为调试残留
        if len(addr) < 20:
            return "非标准短地址"

    return ""


def delete_user_records(cursor, wallet_address: str):
    cursor.execute("DELETE FROM users WHERE wallet_address = ?", (wallet_address,))
    cursor.execute("DELETE FROM points_history WHERE wallet_address = ?", (wallet_address,))
    cursor.execute("DELETE FROM daily_actions WHERE wallet_address = ?", (wallet_address,))
    cursor.execute("DELETE FROM login_nonces WHERE wallet_address = ?", (wallet_address,))
    cursor.execute(
        "DELETE FROM referral_events WHERE inviter_wallet = ? OR invited_wallet = ?",
        (wallet_address, wallet_address),
    )
    cursor.execute("DELETE FROM reward_distributions WHERE wallet_address = ?", (wallet_address,))
    cursor.execute("DELETE FROM liquidation_cases WHERE wallet_address = ?", (wallet_address,))
    cursor.execute(
        "DELETE FROM exchange_redemption_requests WHERE wallet_address = ?",
        (wallet_address,),
    )


def clean_test_wallets(dry_run: bool = False, skip_confirm: bool = False):
    if not os.path.exists(DB_PATH):
        print(f"数据库不存在: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT wallet_address, total_points, created_at, metadata FROM users ORDER BY total_points DESC"
    )
    users = cursor.fetchall()

    test_users = []
    keep_users = []
    for addr, pts, created, meta in users:
        reason = test_wallet_reason(addr, meta)
        if reason:
            test_users.append((addr, pts, created, reason))
        else:
            keep_users.append((addr, pts))

    print(f"数据库用户总数: {len(users)}")
    print(f"将删除测试/垃圾账号: {len(test_users)}")
    print(f"保留（疑似真实钱包）: {len(keep_users)}")
    print()

    for addr, pts, created, reason in test_users:
        print(f"  [删] {addr} | 积分:{pts} | 原因:{reason}")
    if keep_users:
        print()
        print("保留账号（前 10）:")
        for addr, pts in keep_users[:10]:
            print(f"  [留] {addr} | 积分:{pts}")
        if len(keep_users) > 10:
            print(f"  ... 还有 {len(keep_users) - 10} 个")

    if not test_users:
        conn.close()
        print("\n没有需要清理的测试账号。")
        return

    if dry_run:
        conn.close()
        print("\n--dry-run 模式，未执行删除。")
        return

    if not skip_confirm:
        confirm = input("\n确认删除以上测试账号？输入 yes 继续: ").strip().lower()
        if confirm != "yes":
            print("已取消")
            conn.close()
            return

    for addr, _, _, _ in test_users:
        delete_user_records(cursor, addr)

    conn.commit()
    conn.close()
    print(f"\n已删除测试账号: {len(test_users)}")


def clean_all_wallets(skip_confirm: bool = False, dry_run: bool = False):
    if not os.path.exists(DB_PATH):
        print(f"数据库不存在: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()

    print(f"即将清空全部用户数据（真实+测试），当前用户数: {count}")
    print("将清空: users、积分历史、任务记录、邀请、案例、兑换申请、奖励、快照、审计日志等")
    print("保留: exchange_rules（兑换规则配置）")

    if dry_run:
        print("\n--dry-run 模式，未执行删除。")
        return

    if not skip_confirm:
        confirm = input("确认清空？输入 yes 继续: ").strip().lower()
        if confirm != "yes":
            print("已取消")
            return

    counts = points_system.wipe_all_wallet_data()
    print("清空完成:")
    for table, n in counts.items():
        print(f"  {table}: {n} 行")


def main():
    parser = argparse.ArgumentParser(description="清理积分系统钱包/用户数据")
    parser.add_argument("--all", action="store_true", help="清空全部用户（含真实钱包）")
    parser.add_argument("--yes", action="store_true", help="跳过确认")
    parser.add_argument("--dry-run", action="store_true", help="只预览不删除")
    args = parser.parse_args()

    if args.all:
        clean_all_wallets(skip_confirm=args.yes, dry_run=args.dry_run)
    else:
        clean_test_wallets(dry_run=args.dry_run, skip_confirm=args.yes)


if __name__ == "__main__":
    main()
