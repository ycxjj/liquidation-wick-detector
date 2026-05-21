#!/usr/bin/env python3
"""
WickShield v5.1 偿付能力检查与压力测试
- 风险状态机（文档 §4.2，已修复阈值判定逻辑）
- 蒙特卡洛压力测试（文档 §5.1 场景 A-D）
"""
from __future__ import annotations

import math
import random
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .constants import COVERAGE_LIMITS, RISK_ACTIONS, STRESS_SCENARIOS
from ._risk import ratio_and_status


def _poisson(lam: float, rng: random.Random) -> int:
    """Knuth 算法生成泊松随机数"""
    if lam <= 0:
        return 0
    limit = math.exp(-lam)
    k, p = 0, 1.0
    while p > limit:
        k += 1
        p *= rng.random()
    return k - 1


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(len(ordered) * pct / 100)))
    return ordered[idx]


class SolvencyRiskManager:
    @staticmethod
    def check_risk(pool_total: float, total_coverage: float) -> Dict[str, Any]:
        try:
            pool = Decimal(str(pool_total))
            coverage = Decimal(str(total_coverage))
            ratio, status = ratio_and_status(pool, coverage)
            actions = RISK_ACTIONS[status]
            limits = COVERAGE_LIMITS[status]

            return {
                "success": True,
                "data": {
                    "pool_total": float(pool),
                    "total_coverage": float(coverage),
                    "ratio": float(ratio.quantize(Decimal("0.01"))),
                    "status": status,
                    "actions": {
                        "block_new": actions["block_new"],
                        "payout_ratio": float(actions["payout_ratio"]),
                        "pause_new_symbols": actions["pause_new_symbols"],
                    },
                    "coverage_limits": {
                        "single_max": float(limits["single"]),
                        "daily_24h_max": float(limits["daily_24h"]),
                    },
                },
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def stress_test(
        pool_total: float,
        total_coverage: float,
        scenario_type: str = "severe",
        runs: int = 100,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        蒙特卡洛压力测试：泊松模拟插针次数 × 平均单次损失。
        """
        if runs < 1:
            return {"success": False, "error": "runs 须 >= 1"}

        pool = Decimal(str(pool_total))
        coverage = Decimal(str(total_coverage))
        scenario = STRESS_SCENARIOS.get(scenario_type, STRESS_SCENARIOS["severe"])
        rng = random.Random(seed)

        survival_count = 0
        final_ratios: List[float] = []

        for _ in range(runs):
            hits = _poisson(float(scenario["lambda"]), rng)
            total_loss = Decimal(hits * scenario["avg_loss"])
            remaining_pool = pool - total_loss
            if remaining_pool > 0:
                survival_count += 1

            if coverage > 0:
                new_ratio = (remaining_pool / coverage) * Decimal("100")
                final_ratios.append(max(float(new_ratio), 0.0))
            else:
                final_ratios.append(1000.0 if remaining_pool > 0 else 0.0)

        survival_rate = survival_count / runs
        avg_remaining_ratio = sum(final_ratios) / len(final_ratios) if final_ratios else 0.0
        p5_ratio = _percentile(final_ratios, 5) if final_ratios else 0.0

        return {
            "success": True,
            "data": {
                "scenario": scenario_type,
                "scenario_label": scenario["label"],
                "runs": runs,
                "initial_pool": pool_total,
                "total_coverage": total_coverage,
                "survival_rate_percent": round(survival_rate * 100, 2),
                "avg_remaining_ratio_percent": round(avg_remaining_ratio, 2),
                "p5_remaining_ratio_percent": round(p5_ratio, 2),
                "risk_note": (
                    "资金池安全"
                    if survival_rate > 0.95
                    else "需注资、提高保费或启动再保险"
                ),
            },
        }

    @staticmethod
    def run_all_scenarios(
        pool_total: float,
        total_coverage: float,
        runs: int = 50,
        seed: Optional[int] = 42,
    ) -> Dict[str, Any]:
        """依次运行文档 §5.1 四类场景"""
        results = {}
        for key in STRESS_SCENARIOS:
            results[key] = SolvencyRiskManager.stress_test(
                pool_total, total_coverage, key, runs=runs, seed=seed
            )
        return {"success": True, "data": results}


if __name__ == "__main__":
    from .cli import main

    raise SystemExit(
        main(["solvency", "check", "--pool", "50000", "--coverage", "100000"])
    )
