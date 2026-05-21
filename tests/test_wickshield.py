"""WickShield v5.1 精算引擎单元测试"""
import json
from decimal import Decimal

import pytest

from scripts.wickshield._risk import classify_solvency_status, ratio_and_status
from scripts.wickshield.backtest_runner import run_full_backtest
from scripts.wickshield.cli import build_parser, dispatch
from scripts.wickshield.payout_calc import apply_haircut, calc_payout, get_threshold
from scripts.wickshield.premium_calc import calc_premium, credit_discount, get_base_rate
from scripts.wickshield.market_data import compute_atr_percent, get_wick_threshold_pct
from scripts.wickshield.report_risk import (
    aggregate_symbol_from_reports,
    build_report_risk_profile,
    classify_risk_subgrade,
    compute_pricing_coefficient,
    compute_report_premium_multiplier,
)
from scripts.wickshield.claims_stats import monthly_payout_surcharge_factor
from scripts.wickshield.solvency_check import SolvencyRiskManager, _poisson
import pandas as pd
import numpy as np


class TestReportRisk:
    def test_lab_profile_from_local_reports(self) -> None:
        agg = aggregate_symbol_from_reports("LAB/USDT", lookback_days=60)
        if not agg.get("success"):
            pytest.skip("本地无 data/reports 样本")
        assert agg["sample_days"] >= 1
        assert agg["avg_max_amplitude_percent"] > 0

    def test_default_pricing_uses_reports_not_static_table(self) -> None:
        driven = calc_premium(1000, "LAB/USDT", 7, 10, solvency_ratio=45, report_days=60)
        if driven["data"]["pricing_source"] == "cold_start":
            pytest.skip("无日报数据")
        assert driven["data"]["pricing_source"] in ("rate_cache", "daily_reports")
        assert driven["data"].get("insurance_risk")

    def test_legacy_static_only_when_disabled(self) -> None:
        legacy = calc_premium(1000, "BTC/USDT", 1, 1, use_report_risk=False)
        assert legacy["data"]["pricing_source"] == "legacy_static_table"

    def test_multiplier_in_bounds(self) -> None:
        profile = {"success": True, "hits_per_day": 2.0, "avg_max_amplitude_percent": 8.0, "peak_max_amplitude_percent": 10.0}
        m = compute_report_premium_multiplier(profile)
        assert Decimal("0.75") <= m <= Decimal("2.0")


class TestMonitorPerf:
    def test_monitor_days_back_default_v2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.wickshield.market_data import monitor_days_back

        monkeypatch.delenv("WICKSHIELD_MONITOR_DAYS_BACK", raising=False)
        assert monitor_days_back() == pytest.approx(0.3)

    def test_dynamic_workers_by_hour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.wickshield.fetch_guard import dynamic_monitor_workers

        monkeypatch.delenv("WICKSHIELD_MONITOR_WORKERS", raising=False)
        w = dynamic_monitor_workers()
        assert 4 <= w <= 8

    def test_monitor_cache_roundtrip(self) -> None:
        from scripts.wickshield.monitor_cache import load_monitor_snapshot, save_monitor_snapshot

        payload = {"success": True, "test": 1}
        save_monitor_snapshot(payload)
        loaded, hit = load_monitor_snapshot()
        assert hit
        assert loaded.get("test") == 1

    def test_claim_verify_rejects_without_full_spike(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.wickshield.claim_verify import verify_claim_before_payout

        def fake_guard(*_a, **_k):
            import pandas as pd

            ts = pd.date_range("2026-05-20", periods=30, freq="5min")
            df = pd.DataFrame(
                {
                    "timestamp": ts,
                    "open": [100.0] * 30,
                    "high": [100.5] * 30,
                    "low": [99.5] * 30,
                    "close": [100.0] * 30,
                    "volume": [1.0] * 30,
                }
            )
            return df, None

        class FakeDet:
            def detect_wicks(self, recent, **kwargs):
                import pandas as pd

                return pd.DataFrame({"wick_score": [0.1], "amplitude": [1.0]})

        monkeypatch.setattr(
            "scripts.wickshield.fetch_guard.run_with_guard", fake_guard
        )
        monkeypatch.setattr(
            "scripts.wickshield.market_data._wick_detector",
            lambda: (FakeDet, lambda *a, **k: None),
        )
        market = {"lead_exchange": "binanceusdm", "light_mode": True}
        res = verify_claim_before_payout("BTC/USDT", market, 50.0)
        assert res["verified"] is False

    def test_light_spike_count(self) -> None:
        from scripts.wickshield.market_data import count_spikes_last_hours, count_spikes_last_hours_fast

        n = 30
        ts = pd.date_range("2026-05-20", periods=n, freq="5min")
        close = [100.0] * n
        high = [101.0] * n
        low = [99.0] * n
        high[-1] = 110.0
        low[-1] = 90.0
        df = pd.DataFrame(
            {
                "timestamp": ts,
                "open": close,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1.0,
            }
        )
        fast = count_spikes_last_hours_fast(df, "BTC/USDT", hours=1.0)
        full = count_spikes_last_hours(df, "BTC/USDT", hours=1.0, light=True)
        assert fast == full
        assert fast >= 1


class TestMarketDataMulti:
    def test_six_exchange_order(self) -> None:
        from scripts.wickshield.market_data import SIX_EXCHANGES, ohlcv_exchange_order

        order = ohlcv_exchange_order()
        assert order[0] == "binanceusdm"
        assert "bitget" in order
        assert "mexc" in order
        assert len(order) >= len(SIX_EXCHANGES)

    def test_multi_enabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.wickshield.market_data import multi_exchange_enabled

        monkeypatch.delenv("WICKSHIELD_MULTI_EXCHANGE", raising=False)
        assert multi_exchange_enabled() is True
        monkeypatch.setenv("WICKSHIELD_MULTI_EXCHANGE", "0")
        assert multi_exchange_enabled() is False


class TestMarketData:
    def _sample_ohlcv(self, n: int = 50) -> pd.DataFrame:
        ts = pd.date_range("2026-05-20", periods=n, freq="5min")
        close = 100 + np.cumsum(np.random.default_rng(0).normal(0, 0.2, n))
        high = close + 0.5
        low = close - 0.5
        return pd.DataFrame(
            {
                "timestamp": ts,
                "open": close,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000.0,
            }
        )

    def test_atr_computed(self) -> None:
        df = self._sample_ohlcv(60)
        atr = compute_atr_percent(df, 14)
        assert atr is not None
        assert atr > 0

    def test_lab_threshold(self) -> None:
        assert get_wick_threshold_pct("LAB/USDT") == pytest.approx(5.0)


class TestSolvencyClassification:
    @pytest.mark.parametrize(
        "ratio,expected",
        [
            (60, "healthy"),
            (50, "watch"),
            (45, "watch"),
            (25, "warning"),
            (15, "danger"),
            (5, "emergency"),
        ],
    )
    def test_classify_boundaries(self, ratio: float, expected: str) -> None:
        assert classify_solvency_status(Decimal(str(ratio))) == expected

    def test_ratio_zero_coverage(self) -> None:
        r, status = ratio_and_status(100_000, 0)
        assert r == Decimal("1000")
        assert status == "healthy"


class TestRiskSubgrade:
    def test_pricing_coefficient_xaut_like(self) -> None:
        coeff = compute_pricing_coefficient(
            effective_base_pct=Decimal("0.3219"),
            floor_pct=Decimal("0.30"),
            peak_amp_pct=0.61,
            hits_per_day=0,
        )
        assert classify_risk_subgrade(coeff, "A") == "A-"
        assert coeff < Decimal("5")

    def test_subgrade_bands(self) -> None:
        assert classify_risk_subgrade(Decimal("3"), "A") == "A-"
        assert classify_risk_subgrade(Decimal("7"), "A") == "A"
        assert classify_risk_subgrade(Decimal("15"), "A") == "A+"
        assert classify_risk_subgrade(Decimal("10"), "B") == "B"

    def test_monthly_surcharge_escalates(self) -> None:
        assert monthly_payout_surcharge_factor(0) == Decimal("1.00")
        assert monthly_payout_surcharge_factor(1) == Decimal("1.08")
        assert monthly_payout_surcharge_factor(5) == Decimal("1.25")
        assert monthly_payout_surcharge_factor(20) >= Decimal("1.70")

    def test_premium_increases_with_monthly_payouts(self) -> None:
        low = calc_premium(1000, "BTC/USDT", 7, 1, use_report_risk=False, monthly_approved_payouts=0)
        high = calc_premium(1000, "BTC/USDT", 7, 1, use_report_risk=False, monthly_approved_payouts=10)
        assert low["success"] and high["success"]
        assert high["data"]["total_premium"] > low["data"]["total_premium"]
        assert high["data"]["monthly_surcharge_factor"] > low["data"]["monthly_surcharge_factor"]


class TestPremiumCalc:
    def test_lab_reference_uses_report_driven_rate(self) -> None:
        res = calc_premium(1000, "LAB/USDT", 7, 10, solvency_ratio=45)
        assert res["success"] is True
        d = res["data"]
        assert d["pricing_source"] in ("rate_cache", "daily_reports")
        assert d["base_daily_rate_percent"] > 0.8
        assert d["total_premium"] > 0
        assert d["dynamic_cap_ratio"] == pytest.approx(0.25)

    def test_small_policy_not_capped(self) -> None:
        res = calc_premium(100, "BTC/USDT", 1, 1, solvency_ratio=60)
        assert res["success"] is True
        assert res["data"]["was_capped"] is False

    def test_invalid_max_premium_ratio(self) -> None:
        assert calc_premium(100, "BTC/USDT", 1, 1, max_premium_ratio=1.5)["success"] is False

    def test_lab_premium_positive(self) -> None:
        res = calc_premium(1000, "LAB/USDT", 7, 10, solvency_ratio=60)
        assert res["success"] is True
        assert res["data"]["total_premium"] > 0
        assert res["data"]["pricing_source"] in ("rate_cache", "daily_reports")
        assert res["data"]["solvency_status"] == "healthy"

    def test_spike_circuit_lowers_cap(self) -> None:
        res = calc_premium(1000, "LAB/USDT", 7, 10, solvency_ratio=60, spike_count_1h=3, credit_score=300)
        assert res["success"] is True
        assert res["data"]["cap_details"]["spike_override"] is True
        assert res["data"]["dynamic_cap_ratio"] == pytest.approx(0.10)

    def test_fixed_cap_override(self) -> None:
        res = calc_premium(1000, "LAB/USDT", 7, 10, solvency_ratio=45, max_premium_ratio=0.2)
        assert res["data"]["dynamic_cap_ratio"] == pytest.approx(0.2)
        assert res["data"]["total_premium"] == 200.0
        assert res["data"]["cap_details"]["mode"] == "fixed_override"

    def test_solvency_raises_premium(self) -> None:
        healthy = calc_premium(1000, "BTC/USDT", 1, 1, solvency_ratio=60)["data"]["total_premium"]
        stressed = calc_premium(1000, "BTC/USDT", 1, 1, solvency_ratio=15)["data"]["total_premium"]
        assert stressed > healthy

    def test_large_policy_no_discount(self) -> None:
        normal = calc_premium(1000, "BTC/USDT", 1, 1, credit_score=1000, large_policy=False)
        large = calc_premium(1000, "BTC/USDT", 1, 1, credit_score=1000, large_policy=True)
        assert large["data"]["credit_discount"] == 1.0
        assert normal["data"]["credit_discount"] == 0.7
        assert large["data"]["total_premium"] > normal["data"]["total_premium"]

    def test_invalid_amount(self) -> None:
        assert calc_premium(0, "BTC/USDT", 1, 1)["success"] is False

    def test_base_rate_btc_cold_start(self) -> None:
        from scripts.wickshield.premium_calc import get_base_rate_daily_pct

        assert float(get_base_rate_daily_pct("BTC/USDT", prefer_report_data=False)) == pytest.approx(
            0.05
        )


class TestPayoutCalc:
    def test_partial_payout_below_threshold(self) -> None:
        th = float(get_threshold("BTC/USDT"))
        amp = th * 0.5
        res = calc_payout(1000, "BTC/USDT", amp, solvency_ratio=60)
        assert res["success"] is True
        assert res["data"]["final_payout"] == pytest.approx(500, rel=1e-3)
        assert res["data"]["is_full_payout"] is False

    def test_full_payout_at_threshold(self) -> None:
        th = float(get_threshold("LAB/USDT"))
        res = calc_payout(200, "LAB/USDT", th, solvency_ratio=60)
        assert res["data"]["is_full_payout"] is True
        assert res["data"]["final_payout"] == 200

    def test_global_cap_blocks_payout(self) -> None:
        res = calc_payout(
            1000,
            "LAB/USDT",
            6.0,
            global_payout_today=4500,
            solvency_ratio=50,
        )
        assert res["data"]["final_payout"] == 0
        assert res["data"]["is_limited_by_global_cap"] is True

    def test_danger_halves_payout(self) -> None:
        res = calc_payout(1000, "BTC/USDT", 10.0, solvency_ratio=15)
        assert res["data"]["solvency_payout_ratio"] == 0.5
        assert res["data"]["final_payout"] == 100
        assert res["data"]["is_limited_by_single_cap"] is True

    def test_emergency_blocks(self) -> None:
        res = calc_payout(1000, "BTC/USDT", 10.0, solvency_ratio=5)
        assert res["data"]["blocked"] is True
        assert res["data"]["final_payout"] == 0


class TestHaircut:
    def test_proportional_allocation(self) -> None:
        res = apply_haircut([500, 300, 200], 600)
        assert res["success"] is True
        assert res["data"]["haircut_ratio"] == pytest.approx(0.6)
        assert sum(res["data"]["allocations"]) == pytest.approx(600, abs=0.02)

    def test_full_pool_no_haircut(self) -> None:
        res = apply_haircut([100, 100], 500)
        assert res["data"]["haircut_ratio"] == 1.0
        assert res["data"]["allocations"] == [100.0, 100.0]


class TestSolvencyManager:
    def test_check_risk_watch_at_50_percent(self) -> None:
        res = SolvencyRiskManager.check_risk(50_000, 100_000)
        assert res["data"]["status"] == "watch"
        assert res["data"]["ratio"] == 50.0

    def test_stress_test_reproducible(self) -> None:
        a = SolvencyRiskManager.stress_test(50_000, 100_000, "mild", runs=20, seed=7)
        b = SolvencyRiskManager.stress_test(50_000, 100_000, "mild", runs=20, seed=7)
        assert a["data"]["survival_rate_percent"] == b["data"]["survival_rate_percent"]

    def test_poisson_non_negative(self) -> None:
        import random

        rng = random.Random(0)
        for _ in range(50):
            assert _poisson(3.0, rng) >= 0


class TestBacktest:
    def test_full_backtest_success(self) -> None:
        res = run_full_backtest(pool_size=50_000, coverage_size=100_000)
        assert res["success"] is True
        assert "net_flow" in res["data"]


class TestCLI:
    def test_parser_has_subcommands(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_backtest_json_cli(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = dispatch(
            build_parser().parse_args(
                ["backtest", "--pool", "50000", "--coverage", "100000", "--json"]
            )
        )
        out = capsys.readouterr().out
        assert code == 0
        assert '"net_flow"' in out

    def test_premium_cli_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = dispatch(
            build_parser().parse_args(
                ["premium", "--amount", "100", "--symbol", "BTC/USDT", "--days", "1", "--leverage", "1"]
            )
        )
        out = capsys.readouterr().out
        assert code == 0
        assert '"success": true' in out
        assert "total_premium" in out

    def test_monitor_subcommand_exists(self) -> None:
        args = build_parser().parse_args(["monitor", "--dry-run", "--compact"])
        assert args.command == "monitor"
        assert args.dry_run is True


class TestDashboard:
    def test_snapshot_core_fields(self) -> None:
        from scripts.wickshield.dashboard_data import build_dashboard_snapshot

        snap = build_dashboard_snapshot(live_refresh=False, skip_cache=True)
        assert snap["success"] is True
        for key in (
            "solvency_ratio",
            "global_cap_remaining",
            "cap_24h",
            "global_payout_today",
            "watch_symbols",
            "state_date",
        ):
            assert key in snap

    def test_snapshot_fills_estimated_live_columns(self) -> None:
        from scripts.wickshield.dashboard_data import build_dashboard_snapshot

        snap = build_dashboard_snapshot(live_refresh=False, skip_cache=True)
        rows = snap.get("watch_symbols") or []
        assert rows
        live = rows[0].get("live") or {}
        assert live.get("dynamic_cap_ratio") is not None
        assert live.get("global_cap_remaining") is not None
        assert live.get("source") in ("estimated", "cached")

    def test_dashboard_zeros_payout_on_stale_state_date(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.wickshield.monitor as mon
        from scripts.wickshield.dashboard_data import build_dashboard_snapshot

        data_dir = tmp_path / "wickshield"
        data_dir.mkdir()
        state_file = data_dir / "monitor_state.json"
        state_file.write_text(
            '{"date":"2000-01-01","global_payout_today":9999,"claims":[]}',
            encoding="utf-8",
        )
        monkeypatch.setattr(mon, "STATE_FILE", state_file)
        monkeypatch.setattr(mon, "CLAIMS_LOG", data_dir / "claims_log.jsonl")
        monkeypatch.setattr(mon, "RESET_LOG", data_dir / "reset_log.jsonl")
        monkeypatch.setattr(mon, "_DATA", data_dir)

        snap = build_dashboard_snapshot(live_refresh=False, skip_cache=True)
        assert snap["global_payout_today"] == 0.0
        assert snap["global_cap_remaining"] == snap["cap_24h"]


class TestDailyReset:
    def test_reset_clears_payout_and_logs(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.wickshield.monitor as mon

        data_dir = tmp_path / "wickshield"
        data_dir.mkdir()
        state_file = data_dir / "monitor_state.json"
        state_file.write_text(
            '{"date":"2000-01-01","global_payout_today":5000,"claims":[{"x":1}]}',
            encoding="utf-8",
        )
        monkeypatch.setattr(mon, "STATE_FILE", state_file)
        monkeypatch.setattr(mon, "CLAIMS_LOG", data_dir / "claims_log.jsonl")
        monkeypatch.setattr(mon, "RESET_LOG", data_dir / "reset_log.jsonl")
        monkeypatch.setattr(mon, "_DATA", data_dir)

        res = mon.reset_daily_quota(source="test")
        assert res["success"] is True
        assert res["global_payout_today"] == 0.0
        assert res["previous_global_payout_today"] == 5000.0

        st = json.loads(state_file.read_text(encoding="utf-8"))
        assert st["global_payout_today"] == 0.0
        assert st["claims"] == []
        assert mon.RESET_LOG.is_file()

    def test_reset_cli(self, capsys: pytest.CaptureFixture[str], tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.wickshield.monitor as mon

        data_dir = tmp_path / "wickshield"
        data_dir.mkdir()
        monkeypatch.setattr(mon, "STATE_FILE", data_dir / "monitor_state.json")
        monkeypatch.setattr(mon, "CLAIMS_LOG", data_dir / "claims_log.jsonl")
        monkeypatch.setattr(mon, "RESET_LOG", data_dir / "reset_log.jsonl")
        monkeypatch.setattr(mon, "_DATA", data_dir)

        code = dispatch(build_parser().parse_args(["reset", "--compact"]))
        out = capsys.readouterr().out
        assert code == 0
        assert '"global_payout_today": 0' in out or '"global_payout_today":0' in out


class TestMonitorLogic:
    def test_reject_when_global_cap_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.wickshield import monitor as mon

        def fake_market(**_kwargs):
            return {
                "max_amplitude_1h_percent": 99.0,
                "current_atr_percent": 2.0,
                "base_atr_percent": 2.0,
                "spike_count_1h": 0,
            }

        def fake_payout(**kwargs):
            return {
                "success": True,
                "data": {
                    "final_payout": 0,
                    "is_limited_by_global_cap": True,
                    "global_cap_remaining": 0,
                    "cap_24h": 1000,
                    "blocked": False,
                },
            }

        monkeypatch.setattr("scripts.wickshield.market_data.build_market_snapshot", fake_market)
        monkeypatch.setattr(mon, "calc_payout", fake_payout)
        monkeypatch.setattr(mon, "calc_premium", lambda **_: {"success": True, "data": {"dynamic_cap_ratio": 0.5}})
        monkeypatch.setattr(
            mon.SolvencyRiskManager,
            "check_risk",
            lambda *_a, **_k: {"success": True, "data": {"ratio": 50.0}},
        )

        chk = mon.run_live_check("LAB/USDT", global_payout_today=1000)
        assert chk["decision"] == "rejected"
        assert "限额" in chk["reason"] or chk["payout"].get("is_limited_by_global_cap")
