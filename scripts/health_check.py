#!/usr/bin/env python3
"""全功能自检：模块、积分规则、API、徽章、安全与性能配置。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

PASS = 0
FAIL = 0
WARN = 0
ISSUES: list[tuple[str, str, str]] = []


def ok(name: str, detail: str = ""):
    global PASS
    PASS += 1
    print(f"  [OK] {name}" + (f" — {detail}" if detail else ""))


def fail(name: str, detail: str):
    global FAIL
    FAIL += 1
    ISSUES.append(("FAIL", name, detail))
    print(f"  [FAIL] {name}: {detail}")


def warn(name: str, detail: str):
    global WARN
    WARN += 1
    ISSUES.append(("WARN", name, detail))
    print(f"  [WARN] {name}: {detail}")


def section(title: str):
    print(f"\n=== {title} ===")


def main() -> int:
    section("模块与数据库")
    try:
        import points_system as ps

        ps._ensure_db()
        ok("points_system 导入与 _ensure_db")
    except Exception:
        fail("points_system", traceback.format_exc().strip())
        return 1

    with ps._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if str(mode).lower() == "wal":
        ok("SQLite WAL 模式", mode)
    else:
        warn("SQLite journal_mode", str(mode))

    section("钱包地址校验")
    if ps.is_valid_wallet_address("0x" + "a" * 40):
        ok("接受 EVM 地址")
    else:
        fail("EVM 地址", "应通过")
    if not ps.is_valid_wallet_address("not-a-wallet"):
        ok("拒绝无效地址")
    else:
        fail("无效地址", "应拒绝")

    section("周榜 USDT 规则")
    if ps.reward_usdt_for_rank(1) == 20 and ps.reward_usdt_for_rank(10) == 3:
        ok("reward_usdt_for_rank", "1→20, 10→3 USDT")
    else:
        fail("reward_usdt_for_rank", f"1={ps.reward_usdt_for_rank(1)}, 10={ps.reward_usdt_for_rank(10)}")

    rules = ps.get_weekly_rank_reward_rules()
    if len(rules) == ps.WEEKLY_REWARD_TOP_N:
        ok("get_weekly_rank_reward_rules", f"{len(rules)} 条")
    else:
        fail("weekly rules count", str(len(rules)))

    section("分享任务校验")
    if ps.extract_tweet_status_id("https://x.com/user/status/1234567890") == "1234567890":
        ok("extract_tweet_status_id")
    else:
        fail("extract_tweet_status_id", "解析失败")
    if not ps.is_valid_share_tweet_url("https://example.com"):
        ok("拒绝无效推文链接")
    else:
        fail("is_valid_share_tweet_url", "应拒绝非 X 链接")

    section("积分商城服务端定价")
    if ps.POINTS_COST.get("vip_week") == 300:
        ok("POINTS_COST vip_week=300")
    else:
        fail("POINTS_COST", f"vip_week={ps.POINTS_COST.get('vip_week')}")

    section("周冠军徽章")
    with ps._connect() as conn:
        conn.execute("SELECT 1 FROM user_badges LIMIT 1")
    ok("user_badges 表已就绪")

    section("Flask 应用")
    try:
        import app as app_module

        app = app_module.app
        app.config["TESTING"] = True
        client = app.test_client()
        ok("app 导入")
    except Exception as e:
        fail("app 导入", str(e))
        return 1

    if app.secret_key == "wick-detector-dev-secret-change-me":
        warn("FLASK_SECRET_KEY", "生产环境请设置强随机密钥")
    test_login_enabled = os.environ.get("ENABLE_TEST_LOGIN", "1").lower() in (
        "1",
        "true",
        "yes",
    )
    if test_login_enabled:
        warn("ENABLE_TEST_LOGIN", "生产建议 ENABLE_TEST_LOGIN=0")
    else:
        ok("ENABLE_TEST_LOGIN=0（生产推荐）")

    section("安全响应头")
    r = client.get("/api/health")
    if r.status_code == 200 and (r.get_json() or {}).get("status") == "ok":
        ok("/api/health 探活")
    else:
        fail("/api/health", str(r.status_code))
    if r.headers.get("X-Content-Type-Options") == "nosniff":
        ok("X-Content-Type-Options")
    else:
        fail("安全头", "缺少 nosniff")

    section("公开页面与 API")
    public_routes = [
        ("/", "首页/检测入口"),
        ("/detect", "检测页"),
        ("/points", "积分中心"),
        ("/leaderboard", "排行榜页"),
        ("/leaderboard/rewards", "排行榜奖励页"),
        ("/shop", "积分商城"),
        ("/tasks", "每日任务"),
        ("/cases", "爆仓案例"),
        ("/invite", "邀请页"),
        ("/api/points/weekly_rank_rewards", "周奖规则 API"),
        ("/api/points/leaderboard?limit=5", "排行榜 API"),
        ("/api/points/badges/champions?limit=3", "周冠军 API"),
        ("/api/daily/dates", "日报日期列表"),
        ("/api/daily/status", "日报状态"),
    ]
    for path, label in public_routes:
        try:
            r = client.get(path)
            if r.status_code == 200:
                ok(label, path)
            else:
                fail(label, f"{path} → HTTP {r.status_code}")
        except Exception as e:
            fail(label, str(e))

    section("检测页移动端能力")
    detect_html = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
    for fn in ("setDetectBtnReady", "renderHitEventsHtml", "showHitModal"):
        if f"function {fn}" in detect_html:
            ok(f"检测页含 {fn}")
        else:
            fail("检测页 JS", f"缺少 {fn}")

    section("排行榜 API 结构")
    r = client.get("/api/points/leaderboard?limit=3")
    if r.status_code == 200:
        data = r.get_json()
        if "leaderboard" in data and "weekly_rewards" in data:
            ok("leaderboard JSON 含 weekly_rewards")
            if data["weekly_rewards"] and data["weekly_rewards"][0].get("usdt") == 20:
                ok("weekly_rewards[0].usdt=20")
            else:
                fail("weekly_rewards 内容", json.dumps(data.get("weekly_rewards", [])[:2], ensure_ascii=False))
        else:
            fail("leaderboard JSON 字段缺失", str(list(data.keys())))
    else:
        fail("leaderboard API", str(r.status_code))

    lb_big = client.get("/api/points/leaderboard?limit=99999")
    if lb_big.status_code == 200:
        n = len((lb_big.get_json() or {}).get("leaderboard") or [])
        if n <= ps.LEADERBOARD_MAX_LIMIT:
            ok("排行榜 limit 上限", f"返回 {n} ≤ {ps.LEADERBOARD_MAX_LIMIT}")
        else:
            fail("排行榜 limit", f"返回 {n} 条，超过上限")

    section("登录与鉴权")
    r = client.post("/api/points/nonce", json={"wallet_address": "bad"})
    if r.status_code == 400:
        ok("nonce 拒绝无效钱包")
    else:
        fail("nonce 校验", f"HTTP {r.status_code}")

    r = client.get("/api/points/user")
    if r.status_code == 401:
        ok("未登录访问 user → 401")
    else:
        fail("user 未鉴权", str(r.status_code))

    hc_wallet = "0xhealthcheck00000000000000000000000001"
    ps.create_or_get_user(hc_wallet)
    with client.session_transaction() as sess:
        sess["wallet_address"] = hc_wallet
        sess["logged_in"] = True

    r = client.get("/api/points/user")
    if r.status_code == 200 and (r.get_json() or {}).get("wallet_address") == hc_wallet:
        ok("登录后 user API")
        if "badges" in (r.get_json() or {}):
            ok("user API 含 badges 字段")
        else:
            fail("user badges 字段", "缺失")
    else:
        fail("user API", str(r.status_code))

    r = client.get("/api/points/tasks")
    if r.status_code == 200 and "tasks" in (r.get_json() or {}):
        ok("每日任务 API")
    else:
        fail("tasks API", str(r.status_code))

    r = client.post("/api/points/checkin")
    body = r.get_json() or {}
    if body.get("success") is True or "已" in (body.get("message") or ""):
        ok("签到 API 可调用", body.get("message", "")[:40])
    else:
        warn("签到 API", body.get("message", str(r.status_code)))

    r = client.post("/api/points/logout")
    if r.status_code == 200:
        ok("登出 API")
    else:
        fail("logout", str(r.status_code))

    r = client.post("/api/points/test_login")
    if test_login_enabled:
        if r.status_code == 200 and (r.get_json() or {}).get("success"):
            ok("测试登录 API 可用")
        else:
            fail("test_login", str(r.status_code))
    elif r.status_code == 403:
        ok("测试登录已关闭（403 符合预期）")
    else:
        fail("test_login", f"关闭时期望 403，实际 {r.status_code}")

    section("积分兑换安全")
    with client.session_transaction() as sess:
        sess["wallet_address"] = hc_wallet
    r = client.post(
        "/api/points/spend",
        json={"item_type": "vip_week", "cost": 1},
        content_type="application/json",
    )
    body = r.get_json() or {}
    if r.status_code == 400 and body.get("success") is False and "价格" in (body.get("message") or ""):
        ok("拒绝篡改 vip_week 价格为 1")
    elif body.get("success") is True:
        fail("积分兑换价格校验", "客户端 cost=1 仍兑换成功")
    else:
        warn("积分兑换探测", f"HTTP {r.status_code} {body.get('message')}")

    r2 = client.post(
        "/api/points/spend",
        json={"item_type": "nft_badge", "cost": 2000},
        content_type="application/json",
    )
    b2 = r2.get_json() or {}
    if b2.get("success") is False and "暂未开放" in (b2.get("message") or ""):
        ok("拦截未开放的 nft_badge 兑换")
    else:
        fail("nft_badge 应拦截", str(b2))

    r3 = client.post(
        "/api/points/spend",
        json={"item_type": "lottery", "cost": 100},
        content_type="application/json",
    )
    b3 = r3.get_json() or {}
    if b3.get("success") is False and "暂未开放" in (b3.get("message") or ""):
        ok("拦截未开放的 lottery 兑换")
    else:
        fail("lottery 应拦截", str(b3))

    section("管理端鉴权")
    admin_apis = [
        ("POST", "/api/points/snapshots", {"week_start": "2099-01-01", "week_end": "2099-01-07"}),
        ("GET", "/api/points/rewards/pending", None),
        ("GET", "/api/points/cases/pending", None),
        ("GET", "/api/points/redemptions/pending", None),
        ("GET", "/api/admin/audit_logs", None),
        ("GET", "/api/points/snapshots/1", None),
    ]
    with client.session_transaction() as sess:
        sess.clear()
    for method, path, payload in admin_apis:
        if method == "POST":
            r = client.post(path, json=payload or {}, content_type="application/json")
        else:
            r = client.get(path)
        if r.status_code in (401, 403):
            ok(f"未授权 {method} {path}", str(r.status_code))
        else:
            fail(f"管理 API 应拒绝 {path}", f"HTTP {r.status_code}")

    section("周榜快照与冠军徽章（集成）")
    w_top = "0xfunctionaltest000000000000000000000001"
    w2 = "0xfunctionaltest000000000000000000000002"
    ps.create_or_get_user(w_top)
    ps.create_or_get_user(w2)
    with ps._connect() as conn:
        conn.execute("UPDATE users SET total_points = ? WHERE wallet_address = ?", (9000, w_top))
        conn.execute("UPDATE users SET total_points = ? WHERE wallet_address = ?", (100, w2))
        conn.commit()
    snap = ps.create_weekly_snapshot("2099-06-01", "2099-06-07", created_by="health_check")
    if snap.get("id"):
        ok("create_weekly_snapshot", f"id={snap['id']}")
    else:
        fail("周快照", str(snap))
    badges = ps.get_user_badges(w_top, limit=5)
    champ = [b for b in badges if b.get("badge_type") == ps.BADGE_WEEKLY_CHAMPION]
    if champ:
        ok("周冠军徽章已授予榜首", champ[0].get("label", "")[:50])
    else:
        fail("周冠军徽章", f"榜首 {w_top} 无徽章")
    r = client.get("/api/points/badges/champions?limit=5")
    if r.status_code == 200 and (r.get_json() or {}).get("success"):
        ok("champions 公开 API")
    else:
        fail("champions API", str(r.status_code))

    section("模板 HTML 基本检查")
    templates = ROOT / "templates"
    bad_tags = []
    for html in templates.glob("*.html"):
        text = html.read_text(encoding="utf-8", errors="replace")
        if "motion-div" in text:
            bad_tags.append(html.name)
        if html.name == "leaderboard_rewards.html":
            if "nextSettlementMs" in text or "Asia/Shanghai" in text:
                ok("leaderboard_rewards 倒计时使用北京时间")
            else:
                fail("leaderboard_rewards 倒计时", "未找到 Asia/Shanghai 逻辑")
        if html.name in ("daily_tasks.html", "points.html"):
            if "toastModal" in text or "showToast" in text:
                ok(f"{html.name} 含签到/提示弹层")
            else:
                warn(html.name, "未找到 toastModal/showToast")
    if bad_tags:
        fail("无效 HTML 标签 motion-div", ", ".join(bad_tags))
    else:
        ok("模板无 motion-div 残留")

    section("汇总")
    print(f"\n通过 {PASS} | 警告 {WARN} | 失败 {FAIL}")
    if ISSUES:
        print("\n问题清单:")
        for level, name, detail in ISSUES:
            print(f"  [{level}] {name}: {detail}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
