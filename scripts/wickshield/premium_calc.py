#!/usr/bin/env python3
"""
WickShield 保费计算引擎 v3.0
- 扩展币种基础日费率（百分数制，与链下脚本一致）
- 动态保费封顶：偿付能力 × 波动率(ATR) × 插针密度 + 信用调节 + 硬性上下界
- 与 v5.1 协议字段兼容：产品线 tier、大额保单、链上偿付状态标签
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext
from typing import Any, Dict, Optional

from .constants import (
    BASE_RATES_DAILY_PCT,
    MIN_DAILY_RATE,
    PREMIUM_V3_BASE_CAP_RATIO,
    PREMIUM_V3_CAP_ABS_MAX,
    PREMIUM_V3_CAP_ABS_MIN,
    PRODUCT_TIER_MULTIPLIERS,
)
from ._risk import classify_solvency_status

getcontext().prec = 28

LEVERAGE_STEP = Decimal("0.2")


def _extract_insurance_risk(
    report_profile: Optional[Dict[str, Any]], pricing_source: str
) -> Optional[Dict[str, Any]]:
    if not isinstance(report_profile, dict):
        return None
    if pricing_source == "rate_cache":
        entry = report_profile.get("symbol_entry") or {}
        return entry.get("insurance_risk")
    return report_profile.get("insurance_risk")


def _match_symbol(symbol: str) -> str:
    if symbol in BASE_RATES_DAILY_PCT:
        return symbol
    base = symbol.split("/")[0].upper()
    for key in BASE_RATES_DAILY_PCT:
        if key != "DEFAULT" and key.split("/")[0].upper() == base:
            return key
    return "DEFAULT"


def get_base_rate_daily_pct(
    symbol: str,
    *,
    prefer_report_data: bool = True,
    report_days: int = 30,
) -> Decimal:
    """基础日费率（%%）：默认优先费率表/日报，无样本才用冷启动底价。"""
    if prefer_report_data:
        from .report_risk import resolve_base_daily_rate

        base, _src, _ = resolve_base_daily_rate(symbol, lookback_days=report_days)
        return base
    return BASE_RATES_DAILY_PCT.get(_match_symbol(symbol), BASE_RATES_DAILY_PCT["DEFAULT"])


def get_base_rate(symbol: str) -> Decimal:
    """1x 杠杆、未乘产品线时的日费率小数（便于测试与对账）。"""
    return get_base_rate_daily_pct(symbol) / Decimal("100")


def credit_discount(credit_score: int) -> Decimal:
    if credit_score >= 1000:
        return Decimal("0.7")
    if credit_score >= 800:
        return Decimal("0.85")
    if credit_score >= 500:
        return Decimal("0.8")
    if credit_score >= 200:
        return Decimal("0.9")
    return Decimal("1.0")


def long_discount(days: int) -> Decimal:
    if days >= 90:
        return Decimal("0.7")
    if days >= 30:
        return Decimal("0.8")
    if days >= 14:
        return Decimal("0.9")
    return Decimal("1.0")


def amount_discount(amount: Decimal, large_policy: bool) -> Decimal:
    if large_policy:
        return Decimal("1.0")
    if amount >= 1000:
        return Decimal("0.85")
    if amount >= 500:
        return Decimal("0.9")
    if amount >= 100:
        return Decimal("0.95")
    return Decimal("1.0")


def premium_solvency_factor(solvency_ratio: Decimal) -> Decimal:
    """保费定价用偿付能力乘子（与动态封顶第一层不同）。"""
    if solvency_ratio < Decimal("20"):
        return Decimal("1.5")
    if solvency_ratio < Decimal("30"):
        return Decimal("1.2")
    if solvency_ratio < Decimal("50"):
        return Decimal("1.0")
    return Decimal("0.9")


def get_dynamic_cap_ratio(
    solvency_ratio: float,
    credit_score: int,
    current_atr: Optional[float] = None,
    base_atr: Optional[float] = None,
    spike_count_1h: int = 0,
) -> tuple[Decimal, Dict[str, Any]]:
    """
    三层联动动态封顶比例（相对本金）。
    返回 (cap_ratio, cap_details)
    """
    solvency = Decimal(str(solvency_ratio))
    credit = int(credit_score)

    if solvency > Decimal("80"):
        cap_solvency_factor = Decimal("1.2")
    elif solvency > Decimal("50"):
        cap_solvency_factor = Decimal("1.0")
    elif solvency > Decimal("30"):
        cap_solvency_factor = Decimal("0.6")
    else:
        cap_solvency_factor = Decimal("0.4")

    dynamic_cap = PREMIUM_V3_BASE_CAP_RATIO * cap_solvency_factor
    details: Dict[str, Any] = {
        "cap_solvency_factor": float(cap_solvency_factor),
        "volatility_factor": None,
        "spike_override": False,
        "credit_cap_adjustment": "0%",
    }

    volatility_factor: Optional[Decimal] = None
    if current_atr is not None and base_atr is not None:
        cur = Decimal(str(current_atr))
        base = Decimal(str(base_atr))
        if cur > 0 and base > 0:
            volatility_factor = base / cur
            dynamic_cap = dynamic_cap * volatility_factor
            details["volatility_factor"] = float(volatility_factor.quantize(Decimal("0.0001")))

    spike_override = False
    if spike_count_1h >= 3:
        dynamic_cap = Decimal("0.15")
        spike_override = True
    elif spike_count_1h == 2:
        dynamic_cap = dynamic_cap * Decimal("0.7")

    details["spike_override"] = spike_override

    if credit >= 800:
        dynamic_cap += Decimal("0.10")
        details["credit_cap_adjustment"] = "+10%"
    elif credit < 500:
        dynamic_cap -= Decimal("0.05")
        details["credit_cap_adjustment"] = "-5%"

    final_cap = max(PREMIUM_V3_CAP_ABS_MIN, min(dynamic_cap, PREMIUM_V3_CAP_ABS_MAX))
    final_cap = final_cap.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    details["final_cap_ratio"] = float(final_cap)
    return final_cap, details


def calc_premium(
    amount: float,
    symbol: str,
    days: int,
    leverage: int,
    credit_score: int = 300,
    solvency_ratio: float = 60.0,
    product_tier: str = "basic",
    large_policy: bool = False,
    max_premium_ratio: Optional[float] = None,
    current_atr: Optional[float] = None,
    base_atr: Optional[float] = None,
    spike_count_1h: int = 0,
    use_report_risk: bool = True,
    report_days: int = 30,
    monthly_approved_payouts: Optional[int] = None,
) -> Dict[str, Any]:
    """
    计算保费（v3.0 动态封顶 + v5.1 扩展字段）。

    max_premium_ratio: 若指定则**覆盖**动态封顶，直接作为总保费/本金上限（0~1）。
    current_atr / base_atr: 可选，用于波动率对封顶的反比调节（base/current）。
    spike_count_1h: 近 1h 插针计数，>=3 触发封顶冷静期档。
    use_report_risk: 默认 True，基础日费率优先读费率表/日报（非写死 BASE_RATES）。
    report_days: 日报回溯天数（无费率表时实时聚合）。
    monthly_approved_payouts: 当月已批准赔付笔数；None 时从 claims_log 自动统计并叠动态附加费。
    """
    if days < 1:
        return {"success": False, "error": "参数错误: days 须 >= 1"}
    if leverage < 1:
        return {"success": False, "error": "参数错误: leverage 须 >= 1"}
    if amount <= 0:
        return {"success": False, "error": "参数错误: amount 须 > 0"}
    if spike_count_1h < 0:
        return {"success": False, "error": "参数错误: spike_count_1h 不能为负"}

    try:
        amount_dec = Decimal(str(amount))
        days_dec = Decimal(str(days))
        solvency_dec = Decimal(str(solvency_ratio))

        from .report_risk import resolve_base_daily_rate

        pricing_source = "cold_start"
        report_profile: Optional[Dict[str, Any]] = None
        if use_report_risk:
            base_pct, pricing_source, report_profile = resolve_base_daily_rate(
                symbol, report_days
            )
        else:
            base_pct = BASE_RATES_DAILY_PCT.get(
                _match_symbol(symbol), BASE_RATES_DAILY_PCT["DEFAULT"]
            )
            pricing_source = "legacy_static_table"

        leverage_factor = Decimal("1") + (Decimal(leverage) - Decimal("1")) * LEVERAGE_STEP
        tier_mult = PRODUCT_TIER_MULTIPLIERS.get(product_tier, PRODUCT_TIER_MULTIPLIERS["basic"])

        from .claims_stats import monthly_payout_surcharge_detail, monthly_payout_surcharge_factor
        from .report_risk import risk_subgrade_premium_multiplier

        insurance = _extract_insurance_risk(report_profile, pricing_source)
        subgrade_mult = Decimal("1")
        risk_subgrade = None
        pricing_coefficient = None
        if insurance:
            risk_subgrade = insurance.get("risk_subgrade")
            pricing_coefficient = insurance.get("pricing_coefficient")
            if risk_subgrade:
                subgrade_mult = risk_subgrade_premium_multiplier(str(risk_subgrade))

        monthly_surcharge = monthly_payout_surcharge_factor(monthly_approved_payouts)
        monthly_detail = monthly_payout_surcharge_detail(monthly_approved_payouts)

        # 日费率小数：基础费率 × 产品线 × A档细分 × 当月动态附加费
        daily_rate = (
            base_pct
            * leverage_factor
            / Decimal("100")
            * tier_mult
            * subgrade_mult
            * monthly_surcharge
        )
        daily_rate = max(daily_rate, MIN_DAILY_RATE)

        cd = Decimal("1.0") if large_policy else credit_discount(credit_score)
        ld = long_discount(days)
        ad = amount_discount(amount_dec, large_policy)
        total_discount = cd * ld * ad

        psolv = premium_solvency_factor(solvency_dec)
        uncapped = amount_dec * daily_rate * days_dec * total_discount * psolv

        if max_premium_ratio is not None:
            cap_ratio = Decimal(str(max_premium_ratio))
            if cap_ratio < 0 or cap_ratio > 1:
                return {"success": False, "error": "参数错误: max_premium_ratio 须在 0~1 之间"}
            cap_details: Dict[str, Any] = {
                "mode": "fixed_override",
                "final_cap_ratio": float(cap_ratio),
            }
        else:
            cap_ratio, cap_details = get_dynamic_cap_ratio(
                solvency_ratio=solvency_ratio,
                credit_score=credit_score,
                current_atr=current_atr,
                base_atr=base_atr,
                spike_count_1h=spike_count_1h,
            )
            cap_details["mode"] = "dynamic_v3"

        max_allowed = (amount_dec * cap_ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        was_capped = uncapped > max_allowed
        total_premium = min(uncapped, max_allowed).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        premium_rate = (
            (total_premium / amount_dec * Decimal("100")).quantize(Decimal("0.0001"))
            if amount_dec > 0
            else Decimal("0")
        )

        solvency_status = classify_solvency_status(solvency_dec)

        return {
            "success": True,
            "data": {
                "symbol": symbol,
                "product_tier": product_tier,
                "amount": float(amount_dec),
                "days": int(days_dec),
                "leverage": leverage,
                "credit_score": credit_score,
                "large_policy": large_policy,
                "solvency_ratio": solvency_ratio,
                "solvency_status": solvency_status,
                "spike_count_1h": spike_count_1h,
                "pricing_source": pricing_source,
                "base_daily_rate_percent": float(base_pct),
                "use_report_risk": use_report_risk,
                "report_risk": report_profile,
                "insurance_risk": insurance,
                "risk_subgrade": risk_subgrade,
                "pricing_coefficient": pricing_coefficient,
                "risk_subgrade_multiplier": float(subgrade_mult),
                "monthly_surcharge": monthly_detail,
                "monthly_surcharge_factor": float(monthly_surcharge),
                "final_daily_rate_percent": float(daily_rate * 100),
                "total_discount": float(total_discount.quantize(Decimal("0.0001"))),
                "credit_discount": float(cd),
                "tier_multiplier": float(tier_mult),
                "premium_solvency_factor": float(psolv),
                "solvency_factor": float(psolv),
                "dynamic_cap_ratio": float(cap_ratio),
                "uncapped_total_premium": float(uncapped.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "total_premium": float(total_premium),
                "premium_rate_percent": float(premium_rate),
                "was_capped": was_capped,
                "cap_limit_percent": float(cap_ratio * 100),
                "cap_details": cap_details,
            },
        }
    except (InvalidOperation, ValueError) as e:
        return {"success": False, "error": f"参数错误: {e}"}
    except Exception as e:
        return {"success": False, "error": f"计算异常: {e}"}


if __name__ == "__main__":
    from .cli import main

    raise SystemExit(
        main(
            [
                "premium",
                "--amount",
                "1000",
                "--symbol",
                "LAB/USDT",
                "--days",
                "7",
                "--leverage",
                "10",
                "--solvency-ratio",
                "45",
            ]
        )
    )
