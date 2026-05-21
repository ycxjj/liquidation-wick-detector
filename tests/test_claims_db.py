"""claims_db 单元测试。"""
from __future__ import annotations

import pytest


@pytest.fixture()
def claims_db_tmp(monkeypatch, tmp_path):
    from scripts.wickshield import claims_db as mod

    data = tmp_path / "wickshield"
    data.mkdir()
    db = data / "claims.db"
    monkeypatch.setattr(mod, "_DATA", data)
    monkeypatch.setattr(mod, "CLAIMS_DB_PATH", db)
    monkeypatch.setenv("WICKSHIELD_CLAIMS_DB", "1")
    yield db


def test_insert_and_recent(claims_db_tmp):  # noqa: ARG001
    from scripts.wickshield.claims_db import insert_claims_batch, recent_claims, init_db

    init_db()
    entries = [
        {
            "ts": "2026-05-20T10:00:00Z",
            "symbol": "BTC/USDT",
            "decision": "approved",
            "reason": "test",
            "final_payout": 100.0,
        },
        {
            "ts": "2026-05-20T10:01:00Z",
            "symbol": "ETH/USDT",
            "decision": "rejected",
            "reason": "no",
        },
    ]
    n = insert_claims_batch(entries)
    assert n == 2
    rows = recent_claims(10)
    assert len(rows) == 2
    assert rows[0]["symbol"] == "ETH/USDT"
    assert rows[1]["symbol"] == "BTC/USDT"


def test_count_approved_in_month(claims_db_tmp):  # noqa: ARG001
    from scripts.wickshield.claims_db import insert_claim, count_approved_in_month

    insert_claim(
        {
            "ts": "2026-05-15T12:00:00Z",
            "symbol": "SOL/USDT",
            "decision": "approved",
        }
    )
    assert count_approved_in_month("2026-05") == 1
    assert count_approved_in_month("2026-04") == 0
