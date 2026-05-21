#!/usr/bin/env python3
"""
WickShield v5.1 赔付计算引擎
- 按比例/全额赔付
- 动态单人/24h 上限（文档 §9.1）
- 偿付能力赔付比例（文档 §4.2）
- 全局 24h 熔断与 Haircut 预留
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

from .constants import COVERAGE_LIMITS, THRESHOLDS_PIPS
from ._risk import classify_solvency_status


def get_threshold(symbol: str) -> Decimal:
    pips = THRESHOLDS_PIPS.get(symbol, THRESHOLDS_PIPS["DEFAULT"])
    return Decimal(pips) / Decimal(100)


def get_coverage_caps(solvency_ratio: float) -> Dict[str, Decimal]:
    status = classify_solvency_status(Decimal(str(solvency_ratio)))
    return COVERAGE_LIMITS[status]


def calc_payout(
    coverage: float,
    symbol: str,
    actual_amplitude: float,
    global_payout_today: float = 0.0,
    solvency_ratio: float = 60.0,
    solvency_payout_ratio: Optional[float] = None,
) -> Dict[str, Any]:
    """
    计算赔付金额。
    solvency_payout_ratio: 若未传入，则按 solvency_ratio 自动取文档 §4.2 的赔付比例。
    """
    if coverage <= 0:
        return {"success": False, "error": "参数错误: coverage 须 > 0"}
    if actual_amplitude < 0:
        return {"success": False, "error": "参数错误: actual_amplitude 不能为负"}

    try:
        coverage_dec = Decimal(str(coverage))
        actual_dec = Decimal(str(actual_amplitude))
        today_payout_dec = max(Decimal(str(global_payout_today)), Decimal("0"))

        caps = get_coverage_caps(solvency_ratio)
        cap_single = caps["single"]
        cap_24h = caps["daily_24h"]

        if cap_single <= 0:
            return {
                "success": True,
                "data": {
                    "symbol": symbol,
                    "coverage": float(coverage_dec),
                    "actual_amplitude": float(actual_dec),
                    "final_payout": 0.0,
                    "blocked": True,
                    "block_reason": "偿付能力不足，已暂停赔付/新保单",
                },
            }

        threshold = get_threshold(symbol)
        if actual_dec < threshold:
            payout_ratio = actual_dec / threshold
        else:
            payout_ratio = Decimal("1.0")

        theoretical_payout = coverage_dec * payout_ratio

        if solvency_payout_ratio is None:
            from .constants import RISK_ACTIONS

            status = classify_solvency_status(Decimal(str(solvency_ratio)))
            pool_payout_ratio = RISK_ACTIONS[status]["payout_ratio"]
        else:
            pool_payout_ratio = Decimal(str(solvency_payout_ratio))

        after_solvency = theoretical_payout * pool_payout_ratio
        capped_payout = min(after_solvency, cap_single)

        remaining_global_cap = max(cap_24h - today_payout_dec, Decimal("0"))
        final_payout = min(capped_payout, remaining_global_cap)
        final_payout = max(final_payout, Decimal("0")).quantize(Decimal("0.01"))

        is_limited_by_global = capped_payout > remaining_global_cap
        is_limited_by_single = after_solvency > cap_single
        is_limited_by_solvency = pool_payout_ratio < Decimal("1")

        return {
            "success": True,
            "data": {
                "symbol": symbol,
                "coverage": float(coverage_dec),
                "actual_amplitude": float(actual_dec),
                "threshold_percent": float(threshold),
                "payout_ratio_of_threshold": float(payout_ratio),
                "theoretical_payout": float(theoretical_payout),
                "solvency_payout_ratio": float(pool_payout_ratio),
                "after_solvency_payout": float(after_solvency),
                "cap_single": float(cap_single),
                "cap_24h": float(cap_24h),
                "final_payout": float(final_payout),
                "is_full_payout": actual_dec >= threshold,
                "is_limited_by_global_cap": is_limited_by_global,
                "is_limited_by_single_cap": is_limited_by_single,
                "is_limited_by_solvency": is_limited_by_solvency,
                "global_cap_remaining": float(
                    max(remaining_global_cap - final_payout, Decimal("0"))
                ),
                "blocked": False,
            },
        }
    except (InvalidOperation, ValueError) as e:
        return {"success": False, "error": f"参数错误: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def apply_haircut(
    pending_payouts: list[float],
    available_pool: float,
) -> Dict[str, Any]:
    """
    文档 §8.3：优先级1理赔资金不足时，按剩余资金比例 Haircut 分配。
    """
    if not pending_payouts:
        return {"success": True, "data": {"allocations": [], "haircut_ratio": 1.0}}

    pool = Decimal(str(available_pool))
    total_due = sum(Decimal(str(p)) for p in pending_payouts)
    if total_due <= 0:
        return {"success": False, "error": "待赔付总额须 > 0"}
    if pool <= 0:
        ratio = Decimal("0")
    elif pool >= total_due:
        ratio = Decimal("1")
    else:
        ratio = pool / total_due

    allocations = [
        float((Decimal(str(p)) * ratio).quantize(Decimal("0.01"))) for p in pending_payouts
    ]
    return {
        "success": True,
        "data": {
            "haircut_ratio": float(ratio),
            "total_due": float(total_due),
            "available_pool": float(pool),
            "allocations": allocations,
        },
    }


if __name__ == "__main__":
    from .cli import main

    raise SystemExit(
        main(
            [
                "payout",
                "--coverage",
                "1000",
                "--symbol",
                "LAB/USDT",
                "--amplitude",
                "6",
                "--global-payout-today",
                "4500",
                "--solvency-ratio",
                "55",
            ]
        )
    )
