"""用户投保：报价、创建待支付保单、链上保费确认。"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from ._json_util import json_safe
from . import chain_payout
from .policies_db import (
    activate_policy,
    create_pending_policy,
    get_policy,
    list_policies_for_wallet,
)
from .premium_calc import calc_premium
from .solvency_check import SolvencyRiskManager


def _pool_coverage() -> tuple[float, float]:
    pool = float(os.environ.get("WICKSHIELD_POOL", "50000"))
    cov = float(os.environ.get("WICKSHIELD_COVERAGE", "100000"))
    return pool, cov


def quote_policy(
    *,
    symbol: str,
    coverage_amount: float,
    days: int = 7,
    leverage: int = 10,
    wallet_address: Optional[str] = None,
    product_tier: str = "basic",
    credit_score: int = 300,
) -> Dict[str, Any]:
    pool, cov = _pool_coverage()
    solvency = SolvencyRiskManager.check_risk(pool, cov)
    ratio = float(solvency["data"]["ratio"]) if solvency.get("success") else 50.0

    prem = calc_premium(
        amount=coverage_amount,
        symbol=symbol,
        days=days,
        leverage=leverage,
        credit_score=credit_score,
        solvency_ratio=ratio,
        product_tier=product_tier,
        use_report_risk=True,
    )
    if not prem.get("success"):
        return prem

    pr = prem["data"] or {}
    treasury = chain_payout.treasury_address()
    return json_safe(
        {
            "success": True,
            "symbol": symbol,
            "coverage_amount": coverage_amount,
            "days": days,
            "leverage": leverage,
            "product_tier": product_tier,
            "credit_score": credit_score,
            "solvency_ratio": ratio,
            "premium": pr,
            "premium_usdt": float(pr.get("total_premium") or 0),
            "treasury_address": treasury,
            "chain_id": chain_payout.chain_id(),
            "usdt_contract": chain_payout.usdt_contract(),
            "wallet_address": wallet_address,
            "payment_hint": (
                f"请用已连接钱包向资金库 {treasury} 转入 {pr.get('total_premium')} USDT，"
                "然后在投保页提交交易哈希激活保单。"
                if treasury
                else "未配置资金库地址，请联系运维配置 WICKSHIELD_TREASURY_*"
            ),
        }
    )


def create_policy_pending(
    *,
    wallet_address: str,
    symbol: str,
    coverage_amount: float,
    days: int,
    leverage: int,
    product_tier: str,
    credit_score: int,
    payout_address: Optional[str] = None,
) -> Dict[str, Any]:
    q = quote_policy(
        symbol=symbol,
        coverage_amount=coverage_amount,
        days=days,
        leverage=leverage,
        wallet_address=wallet_address,
        product_tier=product_tier,
        credit_score=credit_score,
    )
    if not q.get("success"):
        return q
    if not q.get("treasury_address"):
        return {"success": False, "error": "资金库未配置，暂无法承保"}

    if not payout_address:
        try:
            import points_system

            info = points_system.get_withdrawal_address_info(wallet_address)
            payout_address = info.get("withdrawal_address") or wallet_address
        except Exception:
            payout_address = wallet_address

    policy = create_pending_policy(
        wallet_address=wallet_address,
        symbol=symbol,
        coverage_amount=coverage_amount,
        premium_usdt=float(q["premium_usdt"]),
        days=days,
        leverage=leverage,
        product_tier=product_tier,
        credit_score=credit_score,
        quote=q,
        payout_address=payout_address,
    )
    return json_safe(
        {
            "success": True,
            "policy": policy,
            "treasury_address": q["treasury_address"],
            "premium_usdt": q["premium_usdt"],
            "usdt_contract": q["usdt_contract"],
            "chain_id": q["chain_id"],
        }
    )


def confirm_policy_payment(
    policy_id: int,
    *,
    wallet_address: str,
    premium_tx_hash: str,
) -> Dict[str, Any]:
    policy = get_policy(policy_id)
    if not policy:
        return {"success": False, "error": "保单不存在"}
    if policy["wallet_address"].lower() != wallet_address.lower().strip():
        return {"success": False, "error": "无权操作该保单"}
    if policy["status"] != "pending_payment":
        return {"success": False, "error": f"保单状态为 {policy['status']}，无法确认支付"}

    verified = chain_payout.verify_premium_payment(
        premium_tx_hash,
        from_wallet=wallet_address,
        expected_amount_usdt=float(policy["premium_usdt"]),
    )
    if not verified.get("success"):
        return {"success": False, "error": verified.get("error", "链上验证失败"), "verify": verified}

    activated = activate_policy(policy_id, premium_tx_hash)
    if not activated:
        return {"success": False, "error": "激活失败"}
    return json_safe({"success": True, "policy": activated, "verify": verified})


def get_user_policies(wallet_address: str) -> Dict[str, Any]:
    return json_safe(
        {
            "success": True,
            "policies": list_policies_for_wallet(wallet_address),
        }
    )


def get_policy_pay_info(policy_id: int, *, wallet_address: str) -> Dict[str, Any]:
    """待支付保单：返回继续支付所需信息（无需重新创建保单）。"""
    policy = get_policy(policy_id)
    if not policy:
        return {"success": False, "error": "保单不存在"}
    if policy["wallet_address"].lower() != wallet_address.lower().strip():
        return {"success": False, "error": "无权查看该保单"}
    if policy["status"] != "pending_payment":
        return {
            "success": False,
            "error": f"保单状态为 {policy['status']}，仅待支付保单可继续支付",
        }

    quote = policy.get("quote") if isinstance(policy.get("quote"), dict) else {}
    treasury = quote.get("treasury_address") or chain_payout.treasury_address()
    if not treasury:
        return {"success": False, "error": "资金库未配置，请联系运维"}

    return json_safe(
        {
            "success": True,
            "policy": policy,
            "policy_id": policy["id"],
            "policy_no": policy.get("policy_no"),
            "premium_usdt": float(policy.get("premium_usdt") or 0),
            "treasury_address": treasury,
            "usdt_contract": quote.get("usdt_contract") or chain_payout.usdt_contract(),
            "chain_id": quote.get("chain_id") or chain_payout.chain_id(),
            "payout_address": policy.get("payout_address"),
            "payment_hint": quote.get("payment_hint")
            or f"请向资金库 {treasury} 转入 {policy.get('premium_usdt')} USDT",
        }
    )
