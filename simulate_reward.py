#!/usr/bin/env python3
"""
模拟转币脚本 - 创建奖励发放记录
在服务器上执行此脚本，模拟一次转币操作
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import points_system

def simulate_reward_distribution():
    """模拟一次完整的奖励发放流程"""
    
    print("=" * 60)
    print("  模拟奖励发放流程")
    print("=" * 60)
    
    # 1. 创建周排名快照
    today = datetime.now()
    week_start = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    week_end = today.strftime("%Y-%m-%d")
    
    print(f"\n[1/4] 创建周排名快照: {week_start} ~ {week_end}")
    snapshot = points_system.create_weekly_snapshot(week_start, week_end, "admin")
    snapshot_id = snapshot["id"]
    print(f"  快照 ID: {snapshot_id}")
    
    # 2. 查看待发放的奖励
    print(f"\n[2/4] 查看待发放奖励:")
    pending = points_system.get_pending_rewards(snapshot_id)
    for r in pending:
        print(f"  #{r['rank']} | {r['wallet_address'][:16]}... | {r['points']}分 | {r['reward_amount']} USDT | 状态: {r['status']}")
    
    if not pending:
        print("  没有待发放的奖励（可能没有用户数据）")
        return
    
    # 3. 审核通过所有奖励
    print(f"\n[3/4] 审核通过奖励:")
    for r in pending:
        points_system.approve_reward(r["id"], "admin")
        print(f"  ✅ 奖励 #{r['id']} 已审核通过")
    
    # 4. 模拟转币并记录交易哈希
    print(f"\n[4/4] 模拟转币:")
    for i, r in enumerate(pending):
        # 生成模拟交易哈希
        import hashlib
        fake_txhash = "0x" + hashlib.sha256(
            f"reward_{r['id']}_{r['wallet_address']}_{datetime.now().isoformat()}".encode()
        ).hexdigest()
        
        points_system.record_reward_txhash(r["id"], fake_txhash, "admin")
        print(f"  💰 #{r['rank']} {r['wallet_address'][:16]}... → {r['reward_amount']} USDT")
        print(f"     TxHash: {fake_txhash[:30]}...")
    
    print(f"\n{'=' * 60}")
    print(f"  ✅ 奖励发放完成！共 {len(pending)} 笔")
    print(f"{'=' * 60}")
    
    # 5. 展示所有发放记录
    print(f"\n📋 所有发放记录:")
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'points.db')
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT rd.*, ws.week_start, ws.week_end
            FROM reward_distributions rd
            JOIN weekly_snapshots ws ON rd.snapshot_id = ws.id
            ORDER BY rd.created_at DESC
        """).fetchall()
        
        for row in rows:
            r = dict(row)
            print(f"  周期: {r.get('week_start','')} ~ {r.get('week_end','')}")
            print(f"  排名: #{r['rank']} | 积分: {r['points']} | 奖励: {r['reward_amount']} USDT")
            print(f"  地址: {r['wallet_address']}")
            print(f"  状态: {r['status']} | TxHash: {r.get('txhash', 'N/A')[:40]}...")
            print(f"  时间: {r.get('distributed_at', r['created_at'])}")
            print()


if __name__ == "__main__":
    simulate_reward_distribution()
