"""偿付能力状态与比率换算（供各引擎复用）"""
from decimal import Decimal
from typing import Tuple

from .constants import (
    SOLVENCY_DANGER,
    SOLVENCY_HEALTHY,
    SOLVENCY_WARNING,
    SOLVENCY_WATCH,
)


def solvency_ratio_percent(pool_total: Decimal, total_coverage: Decimal) -> Decimal:
    if total_coverage <= 0:
        return Decimal("1000")
    return (pool_total / total_coverage) * Decimal("100")


def classify_solvency_status(ratio: Decimal) -> str:
    """按设计文档 §4.2 从高到低判定唯一风险等级"""
    if ratio > SOLVENCY_HEALTHY:
        return "healthy"
    if ratio > SOLVENCY_WATCH:
        return "watch"
    if ratio > SOLVENCY_WARNING:
        return "warning"
    if ratio > SOLVENCY_DANGER:
        return "danger"
    return "emergency"


def ratio_and_status(
    pool_total: float | Decimal,
    total_coverage: float | Decimal,
) -> Tuple[Decimal, str]:
    pool = Decimal(str(pool_total))
    coverage = Decimal(str(total_coverage))
    ratio = solvency_ratio_percent(pool, coverage)
    return ratio, classify_solvency_status(ratio)
