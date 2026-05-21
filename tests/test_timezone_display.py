"""展示时区解析（日报存档不改，仅 API/前端展示）"""
from timezone_display import resolve_display_timezone


class TestTimezoneDisplay:
    def test_cloudflare_timezone_header(self) -> None:
        info = resolve_display_timezone({"CF-Timezone": "Asia/Tokyo", "CF-IPCountry": "JP"})
        assert info["timezone"] == "Asia/Tokyo"
        assert info["source"] == "cloudflare_timezone"

    def test_country_fallback_cn(self) -> None:
        info = resolve_display_timezone({"CF-IPCountry": "CN"})
        assert info["timezone"] == "Asia/Shanghai"
        assert info["source"] == "ip_country"

    def test_query_override(self) -> None:
        info = resolve_display_timezone({"CF-IPCountry": "US"}, query_tz="Europe/London")
        assert info["timezone"] == "Europe/London"
        assert info["source"] == "query"

    def test_default(self) -> None:
        info = resolve_display_timezone({})
        assert info["timezone"] == "Asia/Shanghai"
        assert info["source"] == "default"
