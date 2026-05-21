"""理赔统计：当月已赔付次数等，供动态附加费定价。"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .constants import MONTHLY_PAYOUT_SURCHARGE_STEPS

_ROOT = Path(__file__).resolve().parents[2]
_DATA = _ROOT / "data" / "wickshield"
CLAIMS_LOG = _DATA / "claims_log.jsonl"
STATE_FILE = _DATA / "monitor_state.json"


def _month_key(d: Optional[date] = None) -> str:
    if d is None:
        d = _today_local()
    return d.strftime("%Y-%m")


def _today_local() -> date:
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(os.environ.get("DAILY_REPORT_TZ", "Asia/Shanghai"))
        return datetime.now(tz).date()
    except Exception:
        return date.today()


def _parse_ts_month(ts: Any) -> Optional[str]:
    if not ts:
        return None
    s = str(ts).strip()
    if len(s) >= 7 and s[4] == "-":
        return s[:7]
    return None


def _iter_claim_entries() -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    if CLAIMS_LOG.is_file():
        for line in CLAIMS_LOG.read_text(encoding="utf-8").strip().splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if STATE_FILE.is_file():
        try:
            st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            for c in st.get("claims") or []:
                if isinstance(c, dict):
                    entries.append(c)
        except (json.JSONDecodeError, OSError):
            pass
    return entries


def count_monthly_approved_payouts(month: Optional[str] = None) -> int:
    """当月 decision=approved 的赔付笔数（去重 ts+symbol）。"""
    target = month or _month_key()
    seen: set[tuple[str, str]] = set()
    n = 0
    for entry in _iter_claim_entries():
        if str(entry.get("decision", "")).lower() != "approved":
            continue
        m = _parse_ts_month(entry.get("ts"))
        if m != target:
            continue
        key = (str(entry.get("ts", "")), str(entry.get("symbol", "")))
        if key in seen:
            continue
        seen.add(key)
        n += 1
    return n


def monthly_payout_surcharge_factor(approved_count: Optional[int] = None) -> Decimal:
    count = count_monthly_approved_payouts() if approved_count is None else int(approved_count)
    factor = MONTHLY_PAYOUT_SURCHARGE_STEPS[0][1]
    for threshold, mult in MONTHLY_PAYOUT_SURCHARGE_STEPS:
        if count >= threshold:
            factor = mult
    return factor


def monthly_payout_surcharge_detail(approved_count: Optional[int] = None) -> Dict[str, Any]:
    count = count_monthly_approved_payouts() if approved_count is None else int(approved_count)
    factor = monthly_payout_surcharge_factor(count)
    next_threshold = None
    next_factor = None
    for threshold, mult in MONTHLY_PAYOUT_SURCHARGE_STEPS:
        if count < threshold:
            next_threshold = threshold
            next_factor = float(mult)
            break
    return {
        "month": _month_key(),
        "monthly_approved_payout_count": count,
        "monthly_surcharge_factor": float(factor),
        "next_tier_at_count": next_threshold,
        "next_tier_factor": next_factor,
        "tiers": [
            {"min_count": t, "factor": float(m)} for t, m in MONTHLY_PAYOUT_SURCHARGE_STEPS
        ],
    }
