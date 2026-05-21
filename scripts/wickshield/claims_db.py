"""WickShield 理赔记录 SQLite 存储（批量写入，供看板/API 查询）。"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2]
_DATA = _ROOT / "data" / "wickshield"
CLAIMS_DB_PATH = _DATA / "claims.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wickshield_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    amplitude REAL,
    final_payout REAL,
    global_cap_remaining_before REAL,
    global_payout_today_after REAL,
    solvency_ratio REAL,
    dynamic_cap_ratio REAL,
    dry_run INTEGER DEFAULT 0,
    payload_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wickshield_claims_ts ON wickshield_claims(ts DESC);
CREATE INDEX IF NOT EXISTS idx_wickshield_claims_symbol ON wickshield_claims(symbol);
CREATE INDEX IF NOT EXISTS idx_wickshield_claims_decision ON wickshield_claims(decision);
"""


def claims_db_enabled() -> bool:
    return os.environ.get("WICKSHIELD_CLAIMS_DB", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _connect() -> sqlite3.Connection:
    _DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CLAIMS_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def _row_from_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ts": str(entry.get("ts") or datetime.now(timezone.utc).isoformat()),
        "symbol": str(entry.get("symbol") or ""),
        "decision": str(entry.get("decision") or ""),
        "reason": entry.get("reason"),
        "amplitude": entry.get("amplitude"),
        "final_payout": entry.get("final_payout"),
        "global_cap_remaining_before": entry.get("global_cap_remaining_before"),
        "global_payout_today_after": entry.get("global_payout_today_after"),
        "solvency_ratio": entry.get("solvency_ratio"),
        "dynamic_cap_ratio": entry.get("dynamic_cap_ratio"),
        "dry_run": 1 if entry.get("dry_run") else 0,
        "payload_json": json.dumps(entry, ensure_ascii=False),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def insert_claim(entry: Dict[str, Any]) -> int:
    init_db()
    row = _row_from_entry(entry)
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO wickshield_claims (
                ts, symbol, decision, reason, amplitude, final_payout,
                global_cap_remaining_before, global_payout_today_after,
                solvency_ratio, dynamic_cap_ratio, dry_run, payload_json, created_at
            ) VALUES (
                :ts, :symbol, :decision, :reason, :amplitude, :final_payout,
                :global_cap_remaining_before, :global_payout_today_after,
                :solvency_ratio, :dynamic_cap_ratio, :dry_run, :payload_json, :created_at
            )
            """,
            row,
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def insert_claims_batch(entries: List[Dict[str, Any]]) -> int:
    if not entries:
        return 0
    init_db()
    rows = [_row_from_entry(e) for e in entries]
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO wickshield_claims (
                ts, symbol, decision, reason, amplitude, final_payout,
                global_cap_remaining_before, global_payout_today_after,
                solvency_ratio, dynamic_cap_ratio, dry_run, payload_json, created_at
            ) VALUES (
                :ts, :symbol, :decision, :reason, :amplitude, :final_payout,
                :global_cap_remaining_before, :global_payout_today_after,
                :solvency_ratio, :dynamic_cap_ratio, :dry_run, :payload_json, :created_at
            )
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def recent_claims(limit: int = 30, *, decisions: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    if not claims_db_enabled():
        return []
    init_db()
    limit = max(1, min(int(limit), 500))
    sql = "SELECT payload_json FROM wickshield_claims"
    params: List[Any] = []
    if decisions:
        placeholders = ",".join("?" * len(decisions))
        sql += f" WHERE decision IN ({placeholders})"
        params.extend(decisions)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    out: List[Dict[str, Any]] = []
    with _connect() as conn:
        for row in conn.execute(sql, params):
            try:
                out.append(json.loads(row["payload_json"]))
            except (json.JSONDecodeError, TypeError):
                continue
    return out


def count_approved_in_month(month: Optional[str] = None) -> int:
    if not claims_db_enabled():
        return 0
    init_db()
    target = month or datetime.now(timezone.utc).strftime("%Y-%m")
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT ts || '|' || symbol) AS n
            FROM wickshield_claims
            WHERE decision = 'approved' AND substr(ts, 1, 7) = ?
            """,
            (target,),
        ).fetchone()
    return int(row["n"] if row else 0)
