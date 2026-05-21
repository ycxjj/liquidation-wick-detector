"""WickShield 用户保单 SQLite。"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2]
_DATA = _ROOT / "data" / "wickshield"
POLICIES_DB_PATH = _DATA / "policies.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wickshield_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_no TEXT NOT NULL UNIQUE,
    wallet_address TEXT NOT NULL,
    symbol TEXT NOT NULL,
    coverage_amount REAL NOT NULL,
    premium_usdt REAL NOT NULL,
    days INTEGER NOT NULL DEFAULT 7,
    leverage INTEGER NOT NULL DEFAULT 10,
    product_tier TEXT NOT NULL DEFAULT 'basic',
    credit_score INTEGER DEFAULT 300,
    status TEXT NOT NULL DEFAULT 'pending_payment',
    premium_tx_hash TEXT,
    payout_address TEXT,
    quote_json TEXT,
    created_at TEXT NOT NULL,
    activated_at TEXT,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ws_policies_wallet ON wickshield_policies(wallet_address);
CREATE INDEX IF NOT EXISTS idx_ws_policies_symbol_status ON wickshield_policies(symbol, status);
"""


def policies_db_enabled() -> bool:
    return os.environ.get("WICKSHIELD_POLICIES_DB", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _connect() -> sqlite3.Connection:
    _DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(POLICIES_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_policy_no(policy_id: int) -> str:
    return f"WS-{policy_id}-{secrets.token_hex(3).upper()}"


def create_pending_policy(
    *,
    wallet_address: str,
    symbol: str,
    coverage_amount: float,
    premium_usdt: float,
    days: int,
    leverage: int,
    product_tier: str,
    credit_score: int,
    quote: Dict[str, Any],
    payout_address: Optional[str] = None,
) -> Dict[str, Any]:
    init_db()
    wallet_address = wallet_address.lower().strip()
    payout_address = (payout_address or wallet_address).lower().strip()
    now = _now_iso()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO wickshield_policies (
                policy_no, wallet_address, symbol, coverage_amount, premium_usdt,
                days, leverage, product_tier, credit_score, status,
                payout_address, quote_json, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_payment',
                ?, ?, ?
            )
            """,
            (
                f"PENDING-{secrets.token_hex(4)}",
                wallet_address,
                symbol,
                float(coverage_amount),
                float(premium_usdt),
                int(days),
                int(leverage),
                product_tier,
                int(credit_score),
                payout_address,
                json.dumps(quote, ensure_ascii=False),
                now,
            ),
        )
        pid = int(cur.lastrowid or 0)
        policy_no = _new_policy_no(pid)
        conn.execute(
            "UPDATE wickshield_policies SET policy_no = ? WHERE id = ?",
            (policy_no, pid),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM wickshield_policies WHERE id = ?", (pid,)
        ).fetchone()
    return _row_to_dict(row)


def activate_policy(policy_id: int, premium_tx_hash: str) -> Optional[Dict[str, Any]]:
    init_db()
    now = datetime.now(timezone.utc)
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM wickshield_policies WHERE id = ?", (policy_id,)
        ).fetchone()
        if not row or row["status"] != "pending_payment":
            return None
        days = int(row["days"] or 7)
        expires = (now + timedelta(days=days)).isoformat().replace("+00:00", "Z")
        activated = _now_iso()
        conn.execute(
            """
            UPDATE wickshield_policies
            SET status = 'active', premium_tx_hash = ?, activated_at = ?, expires_at = ?
            WHERE id = ?
            """,
            (premium_tx_hash, activated, expires, policy_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM wickshield_policies WHERE id = ?", (policy_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_policy(policy_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM wickshield_policies WHERE id = ?", (policy_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_policies_for_wallet(
    wallet_address: str, *, limit: int = 50
) -> List[Dict[str, Any]]:
    init_db()
    wallet_address = wallet_address.lower().strip()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM wickshield_policies
            WHERE wallet_address = ?
            ORDER BY id DESC LIMIT ?
            """,
            (wallet_address, max(1, min(limit, 200))),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_active_policies() -> List[Dict[str, Any]]:
    """未过期 active 保单。"""
    init_db()
    now = _now_iso()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM wickshield_policies
            WHERE status = 'active'
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY symbol, id
            """,
            (now,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def active_policies_by_symbol() -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for p in list_active_policies():
        sym = p.get("symbol") or ""
        out.setdefault(sym, []).append(p)
    return out


def expire_stale_policies() -> int:
    init_db()
    now = _now_iso()
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE wickshield_policies
            SET status = 'expired'
            WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (now,),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    if d.get("quote_json"):
        try:
            d["quote"] = json.loads(d["quote_json"])
        except json.JSONDecodeError:
            d["quote"] = None
    return d
