"""
从六所 USDT 永续拉取 24h 成交额，合并后得到全市场热门币单（供 WickShield monitor 使用）。
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[2]
MONITOR_SYMBOLS_FILE = _ROOT / "data" / "wickshield" / "monitor_symbols.json"


def _daily_exchange_ids() -> List[str]:
    import sys

    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from daily_scan import _daily_exchange_ids as _ids

    return _ids()


def _always_include() -> List[str]:
    raw = os.environ.get("WICKSHIELD_MONITOR_ALWAYS_INCLUDE", "LAB/USDT")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _fetch_exchange_hot(
    exchange_id: str, per_exchange: int
) -> Tuple[str, List[Tuple[str, float]], Optional[str]]:
    import sys

    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from daily_scan import get_hot_symbols_usdm

    try:
        pairs = get_hot_symbols_usdm(exchange_id, per_exchange)
        usdt = [(s, v) for s, v in pairs if s.endswith("/USDT")]
        return exchange_id, usdt, None
    except Exception as e:
        return exchange_id, [], f"{type(e).__name__}: {e}"


def fetch_top_hot_usdt_symbols(
    limit: int = 50,
    *,
    per_exchange: int = 120,
    workers: int = 4,
) -> Dict[str, Any]:
    """
    六所分别取热门永续，同一币种取最大 24h 成交额后排序，返回 Top N（仅 /USDT）。
    """
    limit = max(1, min(int(limit), 200))
    per_exchange = max(limit, int(per_exchange))
    workers = max(1, min(int(workers), 6))

    agg: Dict[str, float] = {}
    per_ex_count: Dict[str, int] = {}
    errors: Dict[str, str] = {}

    ex_ids = _daily_exchange_ids()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(_fetch_exchange_hot, ex, per_exchange): ex for ex in ex_ids
        }
        for fut in as_completed(futs):
            ex, pairs, err = fut.result()
            if err:
                errors[ex] = err
                continue
            per_ex_count[ex] = len(pairs)
            for sym, vol in pairs:
                agg[sym] = max(agg.get(sym, 0.0), float(vol))

    ranked = sorted(agg.items(), key=lambda x: -x[1])
    symbols: List[str] = []
    seen: set[str] = set()
    for sym in _always_include():
        if sym not in seen:
            symbols.append(sym)
            seen.add(sym)
    for sym, _ in ranked:
        if len(symbols) >= limit:
            break
        if sym in seen:
            continue
        symbols.append(sym)
        seen.add(sym)

    return {
        "success": True,
        "limit": limit,
        "symbol_count": len(symbols),
        "symbols": symbols,
        "symbols_csv": ",".join(symbols),
        "top_volumes": {s: round(v, 2) for s, v in ranked[: min(15, len(ranked))]},
        "exchanges": ex_ids,
        "per_exchange_fetched": per_ex_count,
        "errors": errors or None,
    }


def save_monitor_symbols(payload: Dict[str, Any]) -> Path:
    MONITOR_SYMBOLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "symbols": payload.get("symbols") or [],
        "symbols_csv": payload.get("symbols_csv") or "",
        "limit": payload.get("limit"),
        "generated_by": "wickshield.hot_symbols.fetch_top_hot_usdt_symbols",
        "top_volumes": payload.get("top_volumes"),
        "errors": payload.get("errors"),
    }
    with open(MONITOR_SYMBOLS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    return MONITOR_SYMBOLS_FILE


def load_monitor_symbols_file() -> List[str]:
    if not MONITOR_SYMBOLS_FILE.is_file():
        return []
    try:
        with open(MONITOR_SYMBOLS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        syms = data.get("symbols") or []
        return [str(s).strip() for s in syms if str(s).strip()]
    except (json.JSONDecodeError, OSError):
        return []
