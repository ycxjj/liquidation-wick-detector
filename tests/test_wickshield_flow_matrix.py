"""
WickShield 流程矩阵测试：覆盖决策状态机、CLI 黑盒、防护组件、P2 模块。
与 test_wickshield.py 互补，侧重「端到端步骤」与未覆盖分支。
"""
from __future__ import annotations

import json
import sys
from io import StringIO
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from scripts.wickshield.cli import build_parser, dispatch
from scripts.wickshield.payout_calc import apply_haircut, calc_payout
from scripts.wickshield.premium_calc import calc_premium
from scripts.wickshield.solvency_check import SolvencyRiskManager


def _parse_cli_json(argv: List[str]) -> Dict[str, Any]:
    parser = build_parser()
    args = parser.parse_args(argv)
    buf = StringIO()
    with patch.object(sys, "stdout", buf):
        code = dispatch(args)
    raw = buf.getvalue().strip()
    assert raw, f"CLI 无输出: {' '.join(argv)}"
    data = json.loads(raw.splitlines()[-1] if "\n" in raw else raw)
    return {"exit_code": code, "data": data}


def _fake_market(amp: float = 99.0, light: bool = True) -> Dict[str, Any]:
    return {
        "max_amplitude_1h_percent": amp,
        "current_atr_percent": 2.0,
        "base_atr_percent": 2.0,
        "spike_count_1h": 3,
        "light_mode": light,
        "exchange": "multi",
        "lead_exchange": "binanceusdm",
        "exchanges_used": ["binanceusdm"],
    }


class TestFlowCLIBlackbox:
    """F06：CLI 黑盒 — 每个子命令应返回可解析 JSON 且 success。"""

    def test_cli_premium_compact(self) -> None:
        r = _parse_cli_json(
            ["premium", "--amount", "500", "--symbol", "BTC/USDT", "--days", "3", "--leverage", "5", "--compact"]
        )
        assert r["exit_code"] == 0
        assert r["data"]["success"] is True
        assert "total_premium" in r["data"]["data"]

    def test_cli_payout_compact(self) -> None:
        r = _parse_cli_json(
            [
                "payout",
                "--coverage",
                "1000",
                "--symbol",
                "BTC/USDT",
                "--amplitude",
                "5",
                "--compact",
            ]
        )
        assert r["exit_code"] == 0
        d = r["data"]["data"]
        assert d["final_payout"] >= 0

    def test_cli_solvency_check_compact(self) -> None:
        r = _parse_cli_json(["solvency", "check", "--pool", "50000", "--coverage", "100000", "--compact"])
        assert r["data"]["data"]["ratio"] == pytest.approx(50.0)

    def test_cli_solvency_stress_compact(self) -> None:
        r = _parse_cli_json(
            [
                "solvency",
                "stress",
                "--pool",
                "50000",
                "--coverage",
                "100000",
                "--scenario",
                "mild",
                "--runs",
                "5",
                "--seed",
                "1",
                "--compact",
            ]
        )
        assert "survival_rate_percent" in r["data"]["data"]

    def test_cli_haircut_compact(self) -> None:
        r = _parse_cli_json(
            [
                "haircut",
                "--pending",
                "100",
                "200",
                "--pool",
                "150",
                "--compact",
            ]
        )
        assert r["data"]["success"] is True

    def test_cli_backtest_json(self) -> None:
        r = _parse_cli_json(
            ["backtest", "--pool", "50000", "--coverage", "100000", "--json", "--compact"]
        )
        assert r["data"]["success"] is True

    def test_cli_reset_compact(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.wickshield.monitor as mon

        data_dir = tmp_path / "wickshield"
        data_dir.mkdir()
        monkeypatch.setattr(mon, "STATE_FILE", data_dir / "monitor_state.json")
        monkeypatch.setattr(mon, "CLAIMS_LOG", data_dir / "claims_log.jsonl")
        monkeypatch.setattr(mon, "RESET_LOG", data_dir / "reset_log.jsonl")
        monkeypatch.setattr(mon, "_DATA", data_dir)
        r = _parse_cli_json(["reset", "--compact"])
        assert r["data"]["global_payout_today"] == 0


class TestFlowDecisionMachine:
    """F08：run_live_check 决策分支（白盒 mock）。"""

    def test_no_trigger_low_amplitude(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.wickshield import monitor as mon

        monkeypatch.setattr(mon, "calc_premium", lambda **_: {"success": True, "data": {}})
        monkeypatch.setattr(
            mon.SolvencyRiskManager,
            "check_risk",
            lambda *_a, **_k: {"success": True, "data": {"ratio": 60.0}},
        )
        chk = mon.run_live_check("BTC/USDT", market=_fake_market(amp=0.1))
        assert chk["decision"] == "no_trigger"

    def test_rejected_emergency_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.wickshield import monitor as mon

        monkeypatch.setattr(mon, "calc_premium", lambda **_: {"success": True, "data": {}})
        monkeypatch.setattr(
            mon,
            "calc_payout",
            lambda **_: {
                "success": True,
                "data": {"blocked": True, "block_reason": "偿付能力不足", "final_payout": 0},
            },
        )
        monkeypatch.setattr(
            mon.SolvencyRiskManager,
            "check_risk",
            lambda *_a, **_k: {"success": True, "data": {"ratio": 10.0}},
        )
        chk = mon.run_live_check("BTC/USDT", market=_fake_market(amp=99.0))
        assert chk["decision"] == "rejected"
        assert "偿付" in chk["reason"]

    def test_approved_when_verify_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.wickshield import monitor as mon

        monkeypatch.setattr(
            mon,
            "calc_premium",
            lambda **_: {"success": True, "data": {"dynamic_cap_ratio": 0.5}},
        )
        monkeypatch.setattr(
            mon,
            "calc_payout",
            lambda **_: {
                "success": True,
                "data": {
                    "final_payout": 300,
                    "global_cap_remaining": 5000,
                    "cap_24h": 10000,
                    "blocked": False,
                },
            },
        )
        monkeypatch.setattr(
            mon.SolvencyRiskManager,
            "check_risk",
            lambda *_a, **_k: {"success": True, "data": {"ratio": 60.0}},
        )
        monkeypatch.setattr(
            "scripts.wickshield.claim_verify.claim_full_verify_enabled",
            lambda: False,
        )
        mkt = _fake_market(amp=99.0, light=True)
        chk = mon.run_live_check("BTC/USDT", market=mkt)
        assert chk["decision"] == "approved"
        assert chk["approved_payout"] == 300

    def test_rejected_after_verify_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.wickshield import monitor as mon

        monkeypatch.setattr(
            mon,
            "calc_premium",
            lambda **_: {"success": True, "data": {"dynamic_cap_ratio": 0.5}},
        )
        monkeypatch.setattr(
            mon,
            "calc_payout",
            lambda **_: {
                "success": True,
                "data": {"final_payout": 300, "blocked": False, "global_cap_remaining": 5000},
            },
        )
        monkeypatch.setattr(
            mon.SolvencyRiskManager,
            "check_risk",
            lambda *_a, **_k: {"success": True, "data": {"ratio": 60.0}},
        )
        monkeypatch.setattr(
            "scripts.wickshield.claim_verify.verify_claim_before_payout",
            lambda *a, **k: {
                "success": True,
                "verified": False,
                "reason": "轻量触发但全量形态未确认（疑似假阳性）",
            },
        )
        chk = mon.run_live_check("BTC/USDT", market=_fake_market(amp=99.0, light=True))
        assert chk["decision"] == "rejected"
        assert "复核" in chk["reason"]


class TestFlowMonitorCycle:
    """F07/F17：monitor 周期 dry-run 与 approved 记账。"""

    def _patch_data(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.wickshield.monitor as mon

        data_dir = tmp_path / "wickshield"
        data_dir.mkdir()
        monkeypatch.setattr(mon, "STATE_FILE", data_dir / "monitor_state.json")
        monkeypatch.setattr(mon, "CLAIMS_LOG", data_dir / "claims_log.jsonl")
        monkeypatch.setattr(mon, "RESET_LOG", data_dir / "reset_log.jsonl")
        monkeypatch.setattr(mon, "_DATA", data_dir)
        monkeypatch.setattr(mon, "_watch_symbols", lambda: ["AAA/USDT", "BBB/USDT"])

    def test_dry_run_does_not_increase_global_payout(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.wickshield.monitor import run_monitor_cycle

        self._patch_data(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "scripts.wickshield.monitor._fetch_markets_parallel",
            lambda syms: (
                {s: _fake_market(0.1) for s in syms},
                {},
            ),
        )
        out = run_monitor_cycle(dry_run=True)
        assert out["success"] is True
        assert out["global_payout_today"] == 0.0

    def test_approved_increases_global_payout_and_writes_log(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.wickshield.monitor as mon
        from scripts.wickshield.monitor import run_monitor_cycle

        self._patch_data(tmp_path, monkeypatch)
        monkeypatch.setenv("WICKSHIELD_CLAIMS_DB", "0")

        def always_approve(sym: str) -> Dict[str, Any]:
            return {
                "success": True,
                "checked_at": "2026-05-21T10:00:00Z",
                "symbol": sym,
                "market": _fake_market(99.0, light=False),
                "payout": {"final_payout": 100, "global_cap_remaining": 9000},
                "solvency_ratio": 60.0,
                "dynamic_cap_ratio": 0.5,
                "decision": "approved",
                "reason": "满足插针触发且额度充足",
                "approved_payout": 100.0,
                "threshold_percent": 1.0,
            }

        monkeypatch.setattr(
            "scripts.wickshield.monitor._fetch_markets_parallel",
            lambda syms: ({s: _fake_market() for s in syms}, {}),
        )
        monkeypatch.setattr(mon, "run_live_check", lambda sym, **kw: always_approve(sym))

        out = run_monitor_cycle(dry_run=False)
        assert out["global_payout_today"] == 200.0
        assert mon.CLAIMS_LOG.is_file()
        lines = mon.CLAIMS_LOG.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2


class TestFlowChainWebhook:
    """F15：approved 时 webhook POST。"""

    def test_notify_chain_posts_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.wickshield.monitor import _notify_chain

        posted: List[Dict[str, Any]] = []

        class FakeResp:
            status = 200

        def fake_urlopen(req, timeout=15):
            posted.append(json.loads(req.data.decode("utf-8")))
            return FakeResp()

        monkeypatch.setenv("WICKSHIELD_CHAIN_WEBHOOK", "http://127.0.0.1:9/hook")
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        entry = {"decision": "approved", "symbol": "BTC/USDT", "approved_payout": 1.0}
        _notify_chain(entry)
        assert posted and posted[0]["symbol"] == "BTC/USDT"

    def test_notify_chain_skips_when_not_approved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.wickshield.monitor import _notify_chain

        monkeypatch.setenv("WICKSHIELD_CHAIN_WEBHOOK", "http://127.0.0.1:9/hook")
        with patch("urllib.request.urlopen") as m:
            _notify_chain({"decision": "rejected"})
            m.assert_not_called()


class TestFlowFetchGuard:
    """F13：熔断与降级缓存。"""

    def test_circuit_opens_after_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.wickshield import fetch_guard as fg

        fg.reset_round_metrics()
        monkeypatch.setenv("WICKSHIELD_CIRCUIT_FAIL_THRESHOLD", "2")
        monkeypatch.setenv("WICKSHIELD_CIRCUIT_OPEN_SEC", "60")

        def boom():
            raise TimeoutError("simulated")

        for _ in range(2):
            fg.run_with_guard("okx", "BTC/USDT", "5m", 0.3, boom)
        result, err = fg.run_with_guard("okx", "BTC/USDT", "5m", 0.3, boom)
        assert result is None
        assert err and "熔断" in err
        metrics = fg.get_round_metrics()
        assert metrics["circuit_skip_count"] >= 1

    def test_stale_cache_fallback_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.wickshield import fetch_guard as fg

        df = pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-05-20", periods=25, freq="5min"),
                "open": [1.0] * 25,
                "high": [1.1] * 25,
                "low": [0.9] * 25,
                "close": [1.0] * 25,
                "volume": [1.0] * 25,
            }
        )
        key = fg._cache_key("gate", "ETH/USDT", "5m", 0.3)
        fg._set_cached_ohlcv(key, df)

        def boom():
            raise TimeoutError("simulated")

        monkeypatch.setenv("WICKSHIELD_CIRCUIT_BREAKER", "0")
        result, err = fg.run_with_guard("gate", "ETH/USDT", "5m", 0.3, boom)
        assert result is not None
        assert len(result) == 25
        assert fg.get_round_metrics()["cache_fallback_count"] >= 1


class TestFlowOhlcvCache:
    """F14：热缓存 TTL。"""

    def test_put_get_and_expiry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import time
        from scripts.wickshield.ohlcv_live_cache import cache_ttl_sec, get_cached_df, put_cached_df

        monkeypatch.setenv("WICKSHIELD_OHLCV_CACHE", "1")
        monkeypatch.setenv("WICKSHIELD_OHLCV_CACHE_TTL", "1")
        df = pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-05-20", periods=25, freq="5min"),
                "open": [1.0] * 25,
                "high": [1.1] * 25,
                "low": [0.9] * 25,
                "close": [1.0] * 25,
                "volume": [1.0] * 25,
            }
        )
        put_cached_df("binanceusdm", "BTC/USDT", "5m", 0.3, df)
        assert get_cached_df("binanceusdm", "BTC/USDT", "5m", 0.3) is not None
        time.sleep(cache_ttl_sec() + 0.05)
        assert get_cached_df("binanceusdm", "BTC/USDT", "5m", 0.3) is None


class TestFlowClaimsDb:
    """F11：dashboard 优先读 DB。"""

    def test_read_claims_tail_prefers_db(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.wickshield import claims_db as cdb
        from scripts.wickshield import dashboard_data as dd

        data_dir = tmp_path / "wickshield"
        data_dir.mkdir()
        db_path = data_dir / "claims.db"
        monkeypatch.setattr(cdb, "_DATA", data_dir)
        monkeypatch.setattr(cdb, "CLAIMS_DB_PATH", db_path)
        monkeypatch.setattr(dd, "CLAIMS_LOG", data_dir / "claims_log.jsonl")
        monkeypatch.setenv("WICKSHIELD_CLAIMS_DB", "1")

        cdb.insert_claim(
            {
                "ts": "2026-05-21T12:00:00Z",
                "symbol": "DBONLY/USDT",
                "decision": "approved",
                "final_payout": 42,
            }
        )
        rows = dd._read_claims_tail(5)
        assert rows and rows[0]["symbol"] == "DBONLY/USDT"


class TestFlowPremiumPayoutBranches:
    """F01/F02 补充分支。"""

    def test_premium_tiers_differ(self) -> None:
        b = calc_premium(1000, "BTC/USDT", 7, 10, solvency_ratio=60, product_tier="basic")
        u = calc_premium(1000, "BTC/USDT", 7, 10, solvency_ratio=60, product_tier="ultimate")
        assert b["success"] and u["success"]
        assert u["data"]["total_premium"] >= b["data"]["total_premium"]

    def test_payout_emergency_zero_cap(self) -> None:
        r = calc_payout(1000, "BTC/USDT", 50.0, solvency_ratio=5.0)
        assert r["data"]["blocked"] is True

    def test_haircut_partial_pool(self) -> None:
        r = apply_haircut([100.0, 200.0, 300.0], 300.0)
        assert r["success"] is True
        assert sum(r["data"]["allocations"]) == pytest.approx(300.0, rel=1e-2)


class TestFlowClaimVerify:
    """F09：复核开关。"""

    def test_verify_disabled_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.wickshield.claim_verify import verify_claim_before_payout

        monkeypatch.setenv("WICKSHIELD_CLAIM_FULL_VERIFY", "0")
        res = verify_claim_before_payout("BTC/USDT", _fake_market(), 1.0)
        assert res["verified"] is True
        assert res.get("skipped") is True
