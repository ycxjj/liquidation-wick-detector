"""
WickShield Flask 路由黑盒测试（不启动真实 Gunicorn）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _purge_dashboard_cache() -> None:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from scripts.wickshield import monitor_cache as mc

        if hasattr(mc, "clear_monitor_snapshot"):
            mc.clear_monitor_snapshot()
        elif hasattr(mc, "_mem"):
            mc._mem["payload"] = None
            mc._mem["ts"] = 0.0
    except Exception:
        pass
    host = os.environ.get("WICKSHIELD_REDIS_HOST", "").strip()
    if host:
        try:
            import redis  # type: ignore

            port = int(os.environ.get("WICKSHIELD_REDIS_PORT", "6379"))
            redis.Redis(host=host, port=port, decode_responses=True, socket_timeout=2).delete(
                "wickshield:dashboard"
            )
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _clear_wickshield_dashboard_cache() -> None:
    _purge_dashboard_cache()
    yield
    _purge_dashboard_cache()


@pytest.fixture()
def client():
    try:
        import app as flask_app
    except ImportError as e:
        pytest.skip(f"无法导入 app: {e}")
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        yield c


class TestWickshieldAPIBlackbox:
    """F16：看板页面与 dashboard JSON API。"""

    def test_wickshield_page_returns_html(self, client) -> None:
        r = client.get("/wickshield")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "WickShield" in body or "wickshield" in body.lower()
        assert "claimBody" in body or "最近理赔" in body

    def test_api_dashboard_json_shape(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.wickshield import monitor_cache as mc

        mc.clear_monitor_snapshot()
        monkeypatch.setattr(mc, "load_monitor_snapshot", lambda: (None, False))

        r = client.get("/api/wickshield/dashboard?live=0")
        assert r.status_code == 200
        d = r.get_json()
        assert d is not None
        assert d.get("success") is True
        assert d.get("test") is None, "不应返回测试用缓存 stub"
        assert "solvency_ratio" in d
        for key in (
            "solvency_ratio",
            "global_cap_remaining",
            "cap_24h",
            "global_payout_today",
            "watch_symbols",
            "recent_claims",
            "state_date",
        ):
            assert key in d, f"missing {key}"

    def test_api_dashboard_live_param_accepted(self, client) -> None:
        r = client.get("/api/wickshield/dashboard?live=1")
        assert r.status_code in (200, 500)
        if r.status_code == 200:
            d = r.get_json()
            assert d.get("success") is True
