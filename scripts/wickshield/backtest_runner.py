#!/usr/bin/env python3
"""
WickShield v5.1 综合回测：保费 → 赔付 → 偿付能力 → 压力测试 闭环
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .payout_calc import apply_haircut, calc_payout
from .premium_calc import calc_premium
from .solvency_check import SolvencyRiskManager


def run_full_backtest(
    pool_size: float = 50_000.0,
    coverage_size: float = 100_000.0,
    symbol: str = "LAB/USDT",
    amount: float = 1000.0,
    days: int = 7,
    leverage: int = 10,
    amplitude: float = 6.0,
    global_payout_today: float = 4500.0,
    credit_score: int = 300,
    product_tier: str = "basic",
) -> Dict[str, Any]:
    """执行回测并返回结构化结果（供 CLI / 测试使用）"""
    solvency_res = SolvencyRiskManager.check_risk(pool_size, coverage_size)
    if not solvency_res["success"]:
        return solvency_res

    data = solvency_res["data"]
    current_ratio = data["ratio"]

    premium_res = calc_premium(
        amount=amount,
        symbol=symbol,
        days=days,
        leverage=leverage,
        credit_score=credit_score,
        solvency_ratio=current_ratio,
        product_tier=product_tier,
    )
    payout_res = calc_payout(
        coverage=amount,
        symbol=symbol,
        actual_amplitude=amplitude,
        global_payout_today=global_payout_today,
        solvency_ratio=current_ratio,
    )
    stress_res = SolvencyRiskManager.stress_test(
        pool_size, coverage_size, "severe", runs=100, seed=42
    )
    haircut_res = apply_haircut([500, 300, 200], available_pool=600)

    net_change = 0.0
    if premium_res.get("success") and payout_res.get("success"):
        net_change = premium_res["data"]["total_premium"] - payout_res["data"]["final_payout"]

    return {
        "success": True,
        "data": {
            "solvency": data,
            "premium": premium_res.get("data"),
            "payout": payout_res.get("data"),
            "stress": stress_res.get("data") if stress_res.get("success") else None,
            "haircut": haircut_res.get("data") if haircut_res.get("success") else None,
            "net_flow": net_change,
        },
    }


def print_backtest_report(result: Dict[str, Any]) -> None:
    if not result.get("success"):
        print(f"回测失败: {result.get('error')}")
        return

    d = result["data"]
    sol = d["solvency"]
    print("[WickShield v5.1] 全链路回测开始\n")
    print(
        f"[1] 资金池 | 比率={sol['ratio']}% | 状态={sol['status']} | "
        f"赔付比例={sol['actions']['payout_ratio']} | "
        f"单人上限={sol['coverage_limits']['single_max']}U"
    )

    if d.get("premium"):
        pr = d["premium"]
        print(
            f"[2] 保费   | 收入={pr['total_premium']:.2f}U | "
            f"日费率={pr['final_daily_rate_percent']:.4f}% | "
            f"偿付乘数={pr['solvency_factor']}"
        )

    if d.get("payout"):
        pd = d["payout"]
        print(
            f"[3] 赔付   | 支出={pd['final_payout']:.2f}U | 全额触发={pd.get('is_full_payout')} | "
            f"全局限额={pd.get('is_limited_by_global_cap')}"
        )

    if d.get("stress"):
        sd = d["stress"]
        print(
            f"[4] 压力测试({sd['scenario_label']}) | "
            f"存活率={sd['survival_rate_percent']}% | "
            f"平均剩余比率={sd['avg_remaining_ratio_percent']}%"
        )

    if d.get("haircut"):
        hd = d["haircut"]
        print(
            f"[5] Haircut | 待赔={hd['total_due']}U 可用={hd['available_pool']}U | "
            f"分配={hd['allocations']} ({hd['haircut_ratio']:.0%})"
        )

    print(f"\n[完成] 净流入: {d['net_flow']:+.2f} USDT")


if __name__ == "__main__":
    print_backtest_report(run_full_backtest())
