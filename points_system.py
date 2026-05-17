"""
Wick Detector 积分系统核心模块
支持 Web3 钱包登录、积分获取、等级系统、信用分机制
"""

import os
import re
import sqlite3
import json
import time
from datetime import datetime, date, timedelta
from typing import Optional, List, Tuple
import secrets

_TWEET_STATUS_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/(?:[^/]+/status/|i/web/status/)(\d+)",
    re.IGNORECASE,
)

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Shanghai")
except (ImportError, Exception):
    # Windows 或缺少 tzdata 时的回退方案
    from datetime import timezone, timedelta as td
    TZ = timezone(td(hours=8))  # UTC+8

# 数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "points.db")

# 积分规则配置
POINTS_RULES = {
    "daily_checkin": {"points": 10, "limit_per_day": 1, "name": "每日签到"},
    "detection": {"points": 5, "limit_per_day": 3, "name": "自选检测"},
    "submit_case": {"points": 50, "limit_per_day": 2, "name": "提交爆仓案例"},
    "case_verified": {"points": 100, "limit_per_day": 0, "name": "案例被确认真实"},
    "share_report": {"points": 30, "limit_per_day": 1, "name": "分享日报到X"},
    "invite_user": {"points": 100, "limit_per_day": 0, "name": "邀请新用户"},
    "invite_bonus": {"points": 100, "limit_per_day": 0, "name": "受邀注册奖励"},
    "report_bug": {"points": 200, "limit_per_day": 0, "name": "反馈有效Bug"},
}

# 等级配置
LEVELS = [
    {"level": 1, "name": "入门侦探", "min_points": 0, "max_points": 499},
    {"level": 2, "name": "初级风控师", "min_points": 500, "max_points": 1999},
    {"level": 3, "name": "高级风控师", "min_points": 2000, "max_points": 9999},
    {"level": 4, "name": "创世检测师", "min_points": 10000, "max_points": 999999999},
]

# 每周排行榜 USDT 奖励（前10名，与 create_weekly_snapshot 一致）
WEEKLY_RANK_REWARDS_USDT = {1: 20, 2: 10, 3: 5}
WEEKLY_RANK_REWARD_DEFAULT_USDT = 3  # 第4-10名
WEEKLY_REWARD_TOP_N = 10

# 站内徽章（方案 A：非链上）
BADGE_WEEKLY_CHAMPION = "weekly_champion"
BADGE_LABELS = {
    BADGE_WEEKLY_CHAMPION: "周冠军",
}


def reward_usdt_for_rank(rank: int) -> float:
    if rank < 1:
        return 0
    return float(WEEKLY_RANK_REWARDS_USDT.get(rank, WEEKLY_RANK_REWARD_DEFAULT_USDT))


def get_weekly_rank_reward_rules() -> List[dict]:
    """周排名奖励说明（供前端展示）"""
    rules = []
    for r in range(1, WEEKLY_REWARD_TOP_N + 1):
        rules.append({
            "rank": r,
            "rank_label": f"第{r}名",
            "usdt": reward_usdt_for_rank(r),
        })
    return rules


# 积分消耗配置
POINTS_COST = {
    "unlock_archive": 50,
    "advanced_detection": 50,
    "nft_badge": 2000,
    "vip_week": 300,
    "lottery": 100,
}

LEADERBOARD_MAX_LIMIT = 200


def is_valid_wallet_address(addr: str) -> bool:
    """EVM 或 Tron 提币地址格式（登录/绑定用）。"""
    if not addr or not isinstance(addr, str):
        return False
    a = addr.strip()
    if re.match(r"^0x[a-fA-F0-9]{40}$", a):
        return True
    if re.match(r"^T[1-9A-HJ-NP-Za-km-z]{33}$", a):
        return True
    return False


def _configure_sqlite(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA cache_size=-8000")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    _configure_sqlite(conn)
    return conn


def _ensure_db():
    """初始化数据库表"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                last_login_at TEXT,
                total_points INTEGER DEFAULT 0,
                credit_score INTEGER DEFAULT 100,
                level INTEGER DEFAULT 1,
                invite_code TEXT UNIQUE,
                invited_by TEXT,
                metadata TEXT
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS points_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT NOT NULL,
                action_type TEXT NOT NULL,
                points_change INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                metadata TEXT
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT NOT NULL,
                action_type TEXT NOT NULL,
                action_date TEXT NOT NULL,
                count INTEGER DEFAULT 1,
                last_action_at TEXT NOT NULL,
                UNIQUE(wallet_address, action_type, action_date)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS referral_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inviter_wallet TEXT NOT NULL,
                invited_wallet TEXT UNIQUE NOT NULL,
                invite_code TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS abuse_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT,
                event_type TEXT NOT NULL,
                severity INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                metadata TEXT
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS login_nonces (
                wallet_address TEXT PRIMARY KEY,
                nonce TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        
        # 兑换规则表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS exchange_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                exchange_rate REAL NOT NULL,
                min_amount INTEGER NOT NULL,
                max_amount INTEGER NOT NULL,
                daily_limit INTEGER NOT NULL,
                weekly_limit INTEGER NOT NULL,
                settlement_days INTEGER NOT NULL,
                require_kyc INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                created_by TEXT,
                notes TEXT
            )
        """)
        
        # 兑换规则历史表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS exchange_rules_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                exchange_rate REAL NOT NULL,
                min_amount INTEGER NOT NULL,
                max_amount INTEGER NOT NULL,
                daily_limit INTEGER NOT NULL,
                weekly_limit INTEGER NOT NULL,
                settlement_days INTEGER NOT NULL,
                require_kyc INTEGER DEFAULT 0,
                archived_at TEXT NOT NULL,
                archived_by TEXT,
                reason TEXT
            )
        """)
        
        # 周排名快照表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weekly_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                snapshot_data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT
            )
        """)
        
        # 奖励发放表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reward_distributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                wallet_address TEXT NOT NULL,
                rank INTEGER NOT NULL,
                points INTEGER NOT NULL,
                reward_amount REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                approved_at TEXT,
                approved_by TEXT,
                distributed_at TEXT,
                distributed_by TEXT,
                txhash TEXT,
                notes TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_wallet TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                detail TEXT,
                ip_address TEXT,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS liquidation_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT NOT NULL,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT,
                direction TEXT NOT NULL,
                event_time TEXT,
                price REAL,
                loss_amount REAL,
                description TEXT NOT NULL,
                evidence_urls TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                reviewer_wallet TEXT,
                review_notes TEXT,
                reviewed_at TEXT,
                points_awarded INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS exchange_redemption_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT NOT NULL,
                points_amount INTEGER NOT NULL,
                usdt_amount REAL NOT NULL,
                exchange_rate REAL NOT NULL,
                withdrawal_address TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                reviewer_wallet TEXT,
                review_notes TEXT,
                reviewed_at TEXT,
                txhash TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_badges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT NOT NULL,
                badge_type TEXT NOT NULL,
                snapshot_id INTEGER,
                week_start TEXT,
                week_end TEXT,
                rank INTEGER NOT NULL DEFAULT 1,
                label TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(snapshot_id, badge_type)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_badges_wallet
            ON user_badges(wallet_address, created_at DESC)
        """)
        
        conn.commit()


def _now():
    return datetime.now(TZ).isoformat()


def _today():
    return datetime.now(TZ).date().isoformat()


def get_level_info(total_points: int) -> dict:
    for level in LEVELS:
        if level["min_points"] <= total_points <= level["max_points"]:
            return level
    return LEVELS[0]


def create_or_get_user(wallet_address: str, invited_by: Optional[str] = None) -> dict:
    _ensure_db()
    wallet_address = wallet_address.lower().strip()
    invited_by = (invited_by or "").strip()
    if invited_by == "undefined":
        invited_by = ""
    
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        
        user = conn.execute(
            "SELECT * FROM users WHERE wallet_address = ?",
            (wallet_address,)
        ).fetchone()
        
        if user:
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE wallet_address = ?",
                (_now(), wallet_address)
            )
            conn.commit()
            return dict(user)

        inviter_wallet = None
        invite_code_used = None
        if invited_by:
            inviter = conn.execute(
                "SELECT wallet_address, invite_code FROM users WHERE invite_code = ? OR wallet_address = ?",
                (invited_by, invited_by.lower())
            ).fetchone()
            if inviter and inviter["wallet_address"].lower() != wallet_address:
                inviter_wallet = inviter["wallet_address"].lower()
                invite_code_used = inviter["invite_code"]
        
        invite_code = secrets.token_urlsafe(8)
        conn.execute("""
            INSERT INTO users (wallet_address, created_at, last_login_at, invite_code, invited_by)
            VALUES (?, ?, ?, ?, ?)
        """, (wallet_address, _now(), _now(), invite_code, invite_code_used))
        conn.commit()

        if inviter_wallet:
            exists = conn.execute(
                "SELECT 1 FROM referral_events WHERE invited_wallet = ?",
                (wallet_address,)
            ).fetchone()
            if not exists:
                conn.execute("""
                    INSERT INTO referral_events (inviter_wallet, invited_wallet, invite_code, created_at, metadata)
                    VALUES (?, ?, ?, ?, ?)
                """, (inviter_wallet, wallet_address, invite_code_used, _now(), json.dumps({}, ensure_ascii=False)))
                conn.commit()
                add_points(inviter_wallet, "invite_user", metadata={"invited": wallet_address, "invite_code": invite_code_used})
                add_points(wallet_address, "invite_bonus", metadata={"inviter": inviter_wallet, "invite_code": invite_code_used})
        
        user = conn.execute(
            "SELECT * FROM users WHERE wallet_address = ?",
            (wallet_address,)
        ).fetchone()
        
        return dict(user)


def get_user(wallet_address: str) -> Optional[dict]:
    _ensure_db()
    wallet_address = wallet_address.lower()
    
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        user = conn.execute(
            "SELECT * FROM users WHERE wallet_address = ?",
            (wallet_address,)
        ).fetchone()
        
        if not user:
            return None
        
        user_dict = dict(user)
        user_dict["level_info"] = get_level_info(user_dict["total_points"])
        
        return user_dict


def add_points(wallet_address: str, action_type: str, points: Optional[int] = None, metadata: Optional[dict] = None) -> Tuple[bool, str, int]:
    _ensure_db()
    wallet_address = wallet_address.lower()
    
    user = get_user(wallet_address)
    if not user:
        return False, "用户不存在", 0
    
    if user["credit_score"] < 0:
        return False, "信用分过低，积分账户已冻结", 0
    
    rule = POINTS_RULES.get(action_type)
    if not rule and points is None:
        return False, f"未知的行为类型: {action_type}", 0
    
    points_to_add = points if points is not None else rule["points"]
    
    # 如果是消耗积分（负数），跳过信用分折扣和限制检查
    if points_to_add < 0:
        # 检查余额是否足够
        if user["total_points"] + points_to_add < 0:
            return False, "积分余额不足", 0
    else:
        # 正常获得积分的逻辑
        if user["credit_score"] < 50:
            points_to_add = points_to_add // 2
        
        if rule and rule.get("limit_per_day", 0) > 0:
            can_do, msg = check_daily_limit(wallet_address, action_type, rule["limit_per_day"])
            if not can_do:
                return False, msg, 0
        
        if rule and rule.get("cooldown_minutes", 0) > 0:
            can_do, msg = check_cooldown(wallet_address, action_type, rule["cooldown_minutes"])
            if not can_do:
                return False, msg, 0
    
    with _connect() as conn:
        new_total = user["total_points"] + points_to_add
        conn.execute(
            "UPDATE users SET total_points = ? WHERE wallet_address = ?",
            (new_total, wallet_address)
        )
        
        conn.execute("""
            INSERT INTO points_history (wallet_address, action_type, points_change, balance_after, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            wallet_address,
            action_type,
            points_to_add,
            new_total,
            _now(),
            json.dumps(metadata or {}, ensure_ascii=False)
        ))
        
        # 只有正常获得积分时才记录每日行为
        if points_to_add > 0:
            today = _today()
            now = _now()
            conn.execute("""
                INSERT INTO daily_actions (wallet_address, action_type, action_date, count, last_action_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(wallet_address, action_type, action_date)
                DO UPDATE SET count = count + 1, last_action_at = ?
            """, (wallet_address, action_type, today, now, now))
        
        conn.commit()
    
    update_user_level(wallet_address)
    
    if points_to_add < 0:
        return True, f"消耗 {abs(points_to_add)} 积分", points_to_add
    else:
        return True, f"获得 {points_to_add} 积分", points_to_add


def check_daily_limit(wallet_address: str, action_type: str, limit: int) -> Tuple[bool, str]:
    _ensure_db()
    wallet_address = wallet_address.lower()
    today = _today()
    
    with _connect() as conn:
        row = conn.execute("""
            SELECT count FROM daily_actions
            WHERE wallet_address = ? AND action_type = ? AND action_date = ?
        """, (wallet_address, action_type, today)).fetchone()
        
        if row and row[0] >= limit:
            return False, f"今日 {POINTS_RULES[action_type]['name']} 次数已达上限 ({limit}次)"
        
        return True, "OK"


def check_cooldown(wallet_address: str, action_type: str, cooldown_minutes: int) -> Tuple[bool, str]:
    _ensure_db()
    wallet_address = wallet_address.lower()
    today = _today()
    
    with _connect() as conn:
        row = conn.execute("""
            SELECT last_action_at FROM daily_actions
            WHERE wallet_address = ? AND action_type = ? AND action_date = ?
        """, (wallet_address, action_type, today)).fetchone()
        
        if row:
            last_action = datetime.fromisoformat(row[0])
            now = datetime.now(TZ)
            elapsed = (now - last_action).total_seconds() / 60
            
            if elapsed < cooldown_minutes:
                remaining = int(cooldown_minutes - elapsed)
                return False, f"操作过于频繁，请 {remaining} 分钟后再试"
        
        return True, "OK"


def extract_tweet_status_id(tweet_url: str) -> Optional[str]:
    """从 X/Twitter 推文链接提取 status id"""
    if not tweet_url:
        return None
    m = _TWEET_STATUS_RE.search(tweet_url.strip())
    return m.group(1) if m else None


def normalize_tweet_url(tweet_url: str) -> str:
    status_id = extract_tweet_status_id(tweet_url)
    if not status_id:
        return tweet_url.strip()
    return f"https://x.com/i/web/status/{status_id}"


def is_valid_share_tweet_url(tweet_url: str) -> bool:
    return extract_tweet_status_id(tweet_url) is not None


def tweet_status_id_already_used(status_id: str) -> bool:
    _ensure_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM points_history
            WHERE action_type = 'share_report'
              AND (metadata LIKE ? OR metadata LIKE ?)
            LIMIT 1
            """,
            (f'%"tweet_status_id": "{status_id}"%', f'%/status/{status_id}%'),
        ).fetchone()
        return row is not None


def share_min_delay_seconds() -> int:
    try:
        return max(10, int(os.environ.get("SHARE_MIN_DELAY_SECONDS", "20")))
    except ValueError:
        return 20


def claim_share_report(
    wallet_address: str,
    tweet_url: str,
    share_opened_at: Optional[float] = None,
) -> Tuple[bool, str, int]:
    """领取分享日报积分：需有效推文链接，且距打开分享窗口至少 N 秒。"""
    wallet_address = wallet_address.lower()
    status_id = extract_tweet_status_id(tweet_url)
    if not status_id:
        return False, "请粘贴已发布推文的链接（x.com 或 twitter.com，需含 /status/）", 0

    if tweet_status_id_already_used(status_id):
        return False, "该推文已被其他账号领取过奖励，请使用自己发布的推文", 0

    if share_opened_at is not None:
        try:
            opened = float(share_opened_at)
            need = share_min_delay_seconds()
            if time.time() - opened < need:
                left = int(need - (time.time() - opened)) + 1
                return False, f"请先完成分享，约 {left} 秒后可提交推文链接领取积分", 0
        except (TypeError, ValueError):
            pass

    normalized = normalize_tweet_url(tweet_url)
    return add_points(
        wallet_address,
        "share_report",
        metadata={
            "tweet_url": normalized,
            "tweet_status_id": status_id,
            "verified_by": "tweet_url",
        },
    )


def wipe_all_wallet_data() -> dict:
    """清空积分库中所有用户/钱包相关数据（保留兑换规则配置）。"""
    _ensure_db()
    tables = [
        "exchange_redemption_requests",
        "liquidation_cases",
        "reward_distributions",
        "referral_events",
        "points_history",
        "daily_actions",
        "login_nonces",
        "abuse_events",
        "users",
        "weekly_snapshots",
        "admin_audit_logs",
    ]
    counts = {}
    with _connect() as conn:
        for table in tables:
            cur = conn.execute(f"DELETE FROM {table}")
            counts[table] = cur.rowcount
        conn.commit()
    return counts


def record_daily_action(wallet_address: str, action_type: str):
    _ensure_db()
    wallet_address = wallet_address.lower()
    today = _today()
    now = _now()
    
    with _connect() as conn:
        conn.execute("""
            INSERT INTO daily_actions (wallet_address, action_type, action_date, count, last_action_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(wallet_address, action_type, action_date)
            DO UPDATE SET count = count + 1, last_action_at = ?
        """, (wallet_address, action_type, today, now, now))
        conn.commit()


def update_user_level(wallet_address: str):
    _ensure_db()
    wallet_address = wallet_address.lower()
    
    with _connect() as conn:
        user = conn.execute(
            "SELECT total_points FROM users WHERE wallet_address = ?",
            (wallet_address,)
        ).fetchone()
        
        if not user:
            return
        
        level_info = get_level_info(user[0])
        conn.execute(
            "UPDATE users SET level = ? WHERE wallet_address = ?",
            (level_info["level"], wallet_address)
        )
        conn.commit()


def get_points_history(wallet_address: str, limit: int = 50) -> List[dict]:
    _ensure_db()
    wallet_address = wallet_address.lower()
    limit = max(1, min(int(limit), LEADERBOARD_MAX_LIMIT))

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM points_history
            WHERE wallet_address = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (wallet_address, limit)).fetchall()
        
        return [dict(row) for row in rows]


def get_leaderboard(limit: int = 100) -> List[dict]:
    _ensure_db()
    limit = max(1, min(int(limit), LEADERBOARD_MAX_LIMIT))

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT wallet_address, total_points, level, credit_score, created_at, metadata
            FROM users
            WHERE credit_score >= 0
            ORDER BY total_points DESC, created_at ASC
            LIMIT ?
        """, (limit * 3,)).fetchall()
        
        result = []
        for row in rows:
            user_dict = dict(row)
            try:
                metadata = json.loads(user_dict.get("metadata") or "{}")
            except Exception:
                metadata = {}
            if metadata.get("is_test_user"):
                continue
            user_dict.pop("metadata", None)
            user_dict["rank"] = len(result) + 1
            user_dict["level_info"] = get_level_info(user_dict["total_points"])
            result.append(user_dict)
            if len(result) >= limit:
                break

        return attach_badges_to_users(result)


def generate_nonce(wallet_address: str) -> str:
    _ensure_db()
    wallet_address = wallet_address.lower()
    
    nonce = secrets.token_urlsafe(32)
    now = datetime.now(TZ)
    expires_at = (now + timedelta(minutes=5)).isoformat()
    
    with _connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO login_nonces (wallet_address, nonce, created_at, expires_at)
            VALUES (?, ?, ?, ?)
        """, (wallet_address, nonce, now.isoformat(), expires_at))
        conn.commit()
    
    return nonce


def verify_nonce(wallet_address: str, nonce: str) -> bool:
    _ensure_db()
    wallet_address = wallet_address.lower()
    
    with _connect() as conn:
        row = conn.execute("""
            SELECT nonce, expires_at FROM login_nonces
            WHERE wallet_address = ?
        """, (wallet_address,)).fetchone()
        
        if not row:
            return False
        
        stored_nonce, expires_at = row
        
        if stored_nonce != nonce:
            return False
        
        if datetime.fromisoformat(expires_at) < datetime.now(TZ):
            return False
        
        conn.execute("DELETE FROM login_nonces WHERE wallet_address = ?", (wallet_address,))
        conn.commit()
        
        return True


# ==================== 兑换规则管理 ====================

def get_current_exchange_rule() -> Optional[dict]:
    """获取当前生效的兑换规则"""
    _ensure_db()
    
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rule = conn.execute("""
            SELECT * FROM exchange_rules
            WHERE is_active = 1
            ORDER BY version DESC
            LIMIT 1
        """).fetchone()
        
        if rule:
            return dict(rule)
        return None


def create_exchange_rule(
    exchange_rate: float,
    min_amount: int,
    max_amount: int,
    daily_limit: int,
    weekly_limit: int,
    settlement_days: int,
    require_kyc: bool = False,
    created_by: str = "admin",
    notes: str = ""
) -> dict:
    """创建新的兑换规则（会归档旧规则）"""
    _ensure_db()
    
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        
        # 获取当前规则
        current_rule = conn.execute("""
            SELECT * FROM exchange_rules WHERE is_active = 1
        """).fetchone()
        
        # 如果有当前规则，归档它
        if current_rule:
            conn.execute("""
                INSERT INTO exchange_rules_history 
                (rule_id, version, exchange_rate, min_amount, max_amount, 
                 daily_limit, weekly_limit, settlement_days, require_kyc, 
                 archived_at, archived_by, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                current_rule['id'], current_rule['version'], current_rule['exchange_rate'],
                current_rule['min_amount'], current_rule['max_amount'],
                current_rule['daily_limit'], current_rule['weekly_limit'],
                current_rule['settlement_days'], current_rule['require_kyc'],
                _now(), created_by, "新规则发布"
            ))
            
            # 停用旧规则
            conn.execute("""
                UPDATE exchange_rules SET is_active = 0 WHERE id = ?
            """, (current_rule['id'],))
        
        # 创建新规则
        new_version = (current_rule['version'] + 1) if current_rule else 1
        
        cursor = conn.execute("""
            INSERT INTO exchange_rules 
            (version, exchange_rate, min_amount, max_amount, daily_limit, 
             weekly_limit, settlement_days, require_kyc, is_active, created_at, 
             created_by, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """, (
            new_version, exchange_rate, min_amount, max_amount, daily_limit,
            weekly_limit, settlement_days, 1 if require_kyc else 0, _now(),
            created_by, notes
        ))
        
        conn.commit()
        
        # 返回新创建的规则
        new_rule = conn.execute("""
            SELECT * FROM exchange_rules WHERE id = ?
        """, (cursor.lastrowid,)).fetchone()
        
        return dict(new_rule)


def get_exchange_rules_history(limit: int = 10) -> List[dict]:
    """获取兑换规则历史记录"""
    _ensure_db()
    
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM exchange_rules_history
            ORDER BY archived_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        
        return [dict(row) for row in rows]


def init_default_exchange_rule():
    """初始化默认兑换规则（如果不存在）"""
    _ensure_db()
    
    current = get_current_exchange_rule()
    if not current:
        create_exchange_rule(
            exchange_rate=100.0,  # 100积分 = 1 USDT
            min_amount=1000,      # 最低兑换1000积分
            max_amount=100000,    # 最高兑换100000积分
            daily_limit=10000,    # 每日限额10000积分
            weekly_limit=50000,   # 每周限额50000积分
            settlement_days=1,    # T+1到账
            require_kyc=False,
            created_by="system",
            notes="初始默认规则"
        )
        print("✅ 已创建默认兑换规则")


# ==================== 站内徽章 ====================

def grant_weekly_champion_badge(
    conn: sqlite3.Connection,
    wallet_address: str,
    snapshot_id: int,
    week_start: str,
    week_end: str,
) -> Optional[dict]:
    """周榜快照第 1 名授予站内「周冠军」徽章（每快照仅一枚）。"""
    wallet_address = wallet_address.lower()
    label = f"周冠军 {week_start} ~ {week_end}"
    try:
        cur = conn.execute(
            """
            INSERT INTO user_badges
            (wallet_address, badge_type, snapshot_id, week_start, week_end, rank, label, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                wallet_address,
                BADGE_WEEKLY_CHAMPION,
                snapshot_id,
                week_start,
                week_end,
                label,
                _now(),
            ),
        )
        return {
            "id": cur.lastrowid,
            "wallet_address": wallet_address,
            "badge_type": BADGE_WEEKLY_CHAMPION,
            "snapshot_id": snapshot_id,
            "week_start": week_start,
            "week_end": week_end,
            "rank": 1,
            "label": label,
        }
    except sqlite3.IntegrityError:
        return None


def get_user_badges(wallet_address: str, limit: int = 50) -> List[dict]:
    _ensure_db()
    wallet_address = wallet_address.lower()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM user_badges
            WHERE wallet_address = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (wallet_address, limit),
        ).fetchall()
        return [_format_badge_row(dict(r)) for r in rows]


def get_badges_grouped_by_wallets(wallet_addresses: List[str]) -> dict:
    """批量查询钱包徽章，返回 {wallet_lower: [badge, ...]}"""
    _ensure_db()
    normalized = list({w.lower() for w in wallet_addresses if w})
    if not normalized:
        return {}
    placeholders = ",".join("?" * len(normalized))
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT * FROM user_badges
            WHERE wallet_address IN ({placeholders})
            ORDER BY created_at DESC
            """,
            normalized,
        ).fetchall()
        grouped: dict = {}
        for row in rows:
            w = row["wallet_address"]
            grouped.setdefault(w, []).append(_format_badge_row(dict(row)))
        return grouped


def _format_badge_row(badge: dict) -> dict:
    badge["badge_name"] = BADGE_LABELS.get(badge.get("badge_type"), badge.get("badge_type"))
    return badge


def attach_badges_to_users(users: List[dict]) -> List[dict]:
    if not users:
        return users
    grouped = get_badges_grouped_by_wallets([u["wallet_address"] for u in users])
    for user in users:
        w = user["wallet_address"].lower()
        badges = grouped.get(w, [])
        user["badges"] = badges
        user["weekly_champion_count"] = sum(
            1 for b in badges if b.get("badge_type") == BADGE_WEEKLY_CHAMPION
        )
    return users


def get_recent_weekly_champions(limit: int = 10) -> List[dict]:
    _ensure_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM user_badges
            WHERE badge_type = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (BADGE_WEEKLY_CHAMPION, limit),
        ).fetchall()
        return [_format_badge_row(dict(r)) for r in rows]


# ==================== 周排名快照系统 ====================

def create_weekly_snapshot(week_start: str, week_end: str, created_by: str = "system") -> dict:
    """创建周排名快照"""
    _ensure_db()
    
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        
        # 获取排行榜数据
        leaderboard = get_leaderboard(limit=100)
        
        # 创建快照记录
        cursor = conn.execute("""
            INSERT INTO weekly_snapshots 
            (week_start, week_end, snapshot_data, created_at, created_by)
            VALUES (?, ?, ?, ?, ?)
        """, (week_start, week_end, json.dumps(leaderboard), _now(), created_by))
        
        snapshot_id = cursor.lastrowid
        
        top_users = leaderboard[:WEEKLY_REWARD_TOP_N]
        for rank, user in enumerate(top_users, 1):
            reward_amount = reward_usdt_for_rank(rank)
            
            conn.execute("""
                INSERT INTO reward_distributions
                (snapshot_id, wallet_address, rank, points, reward_amount, 
                 status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """, (snapshot_id, user['wallet_address'], rank, 
                  user['total_points'], reward_amount, _now()))

        weekly_champion_badge = None
        if top_users:
            weekly_champion_badge = grant_weekly_champion_badge(
                conn,
                top_users[0]["wallet_address"],
                snapshot_id,
                week_start,
                week_end,
            )
        
        conn.commit()
        
        snapshot = conn.execute("""
            SELECT * FROM weekly_snapshots WHERE id = ?
        """, (snapshot_id,)).fetchone()
        
        result = dict(snapshot)
        if weekly_champion_badge:
            result["weekly_champion_badge"] = weekly_champion_badge
        return result


def get_weekly_snapshots(limit: int = 10) -> List[dict]:
    """获取历史快照"""
    _ensure_db()
    
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM weekly_snapshots
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        
        return [dict(row) for row in rows]


def get_snapshot_rewards(snapshot_id: int, status: Optional[str] = None) -> List[dict]:
    """获取某次快照的全部奖励记录（可按状态筛选）"""
    _ensure_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        if status:
            rows = conn.execute(
                """
                SELECT * FROM reward_distributions
                WHERE snapshot_id = ? AND status = ?
                ORDER BY rank ASC
                """,
                (snapshot_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM reward_distributions
                WHERE snapshot_id = ?
                ORDER BY rank ASC
                """,
                (snapshot_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def get_weekly_snapshot_detail(snapshot_id: int) -> Optional[dict]:
    """快照详情：周期信息 + 前十名奖励 + 快照时排行榜"""
    _ensure_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        snap = conn.execute(
            "SELECT * FROM weekly_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        if not snap:
            return None

        result = dict(snap)
        try:
            result["leaderboard"] = json.loads(result.get("snapshot_data") or "[]")
        except Exception:
            result["leaderboard"] = []

        rewards = get_snapshot_rewards(snapshot_id)
        result["rewards"] = rewards
        result["reward_rules"] = get_weekly_rank_reward_rules()
        result["total_usdt"] = sum(float(r.get("reward_amount") or 0) for r in rewards)
        result["status_summary"] = {
            "pending": sum(1 for r in rewards if r.get("status") == "pending"),
            "approved": sum(1 for r in rewards if r.get("status") == "approved"),
            "distributed": sum(1 for r in rewards if r.get("status") == "distributed"),
        }
        champ = conn.execute(
            """
            SELECT * FROM user_badges
            WHERE snapshot_id = ? AND badge_type = ?
            """,
            (snapshot_id, BADGE_WEEKLY_CHAMPION),
        ).fetchone()
        result["weekly_champion"] = _format_badge_row(dict(champ)) if champ else None
        return result


# ==================== 奖励发放系统 ====================

def get_pending_rewards(snapshot_id: Optional[int] = None) -> List[dict]:
    """获取待发放的奖励"""
    _ensure_db()
    
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        
        if snapshot_id:
            rows = conn.execute("""
                SELECT * FROM reward_distributions
                WHERE snapshot_id = ? AND status = 'pending'
                ORDER BY rank ASC
            """, (snapshot_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM reward_distributions
                WHERE status = 'pending'
                ORDER BY created_at DESC
            """).fetchall()
        
        return [dict(row) for row in rows]


def approve_reward(reward_id: int, approved_by: str) -> dict:
    """审核通过奖励"""
    _ensure_db()
    
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        
        conn.execute("""
            UPDATE reward_distributions
            SET status = 'approved', approved_at = ?, approved_by = ?
            WHERE id = ?
        """, (_now(), approved_by, reward_id))
        
        conn.commit()
        
        reward = conn.execute("""
            SELECT * FROM reward_distributions WHERE id = ?
        """, (reward_id,)).fetchone()
        
        return dict(reward)


def record_reward_txhash(reward_id: int, txhash: str, distributed_by: str) -> dict:
    """记录奖励的链上交易哈希"""
    _ensure_db()
    
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        
        conn.execute("""
            UPDATE reward_distributions
            SET status = 'distributed', txhash = ?, 
                distributed_at = ?, distributed_by = ?
            WHERE id = ?
        """, (txhash, _now(), distributed_by, reward_id))
        
        conn.commit()
        
        reward = conn.execute("""
            SELECT * FROM reward_distributions WHERE id = ?
        """, (reward_id,)).fetchone()
        
        return dict(reward)


def get_user_rewards(wallet_address: str) -> List[dict]:
    """获取用户的奖励记录"""
    _ensure_db()
    wallet_address = wallet_address.lower()
    
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM reward_distributions
            WHERE wallet_address = ?
            ORDER BY created_at DESC
        """, (wallet_address,)).fetchall()
        
        return [dict(row) for row in rows]


def get_all_distributed_rewards(limit: int = 50) -> List[dict]:
    """获取所有已发放的奖励记录（公开展示）"""
    _ensure_db()
    
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT rd.*, ws.week_start, ws.week_end
            FROM reward_distributions rd
            LEFT JOIN weekly_snapshots ws ON rd.snapshot_id = ws.id
            WHERE rd.status = 'distributed' AND rd.txhash IS NOT NULL
            ORDER BY rd.snapshot_id DESC, rd.rank ASC
            LIMIT ?
        """, (limit,)).fetchall()
        
        results = []
        for row in rows:
            r = dict(row)
            # 脱敏钱包地址：只显示前6位和后4位
            addr = r.get("wallet_address", "")
            if len(addr) > 12:
                r["wallet_address_short"] = addr[:6] + "..." + addr[-4:]
            else:
                r["wallet_address_short"] = addr
            results.append(r)
        
        return results


# ==================== 钱包地址管理 ====================

def set_user_metadata(wallet_address: str, updates: dict) -> bool:
    _ensure_db()
    wallet_address = wallet_address.lower()
    with _connect() as conn:
        row = conn.execute("SELECT metadata FROM users WHERE wallet_address = ?", (wallet_address,)).fetchone()
        if not row:
            return False
        metadata = json.loads(row[0]) if row[0] else {}
        metadata.update(updates or {})
        conn.execute("UPDATE users SET metadata = ? WHERE wallet_address = ?", (json.dumps(metadata, ensure_ascii=False), wallet_address))
        conn.commit()
        return True


def is_test_user(wallet_address: str) -> bool:
    _ensure_db()
    wallet_address = wallet_address.lower()
    with _connect() as conn:
        row = conn.execute("SELECT metadata FROM users WHERE wallet_address = ?", (wallet_address,)).fetchone()
        if not row or not row[0]:
            return False
        try:
            metadata = json.loads(row[0])
        except Exception:
            return False
        return bool(metadata.get("is_test_user"))


def withdrawal_cooldown_hours() -> int:
    try:
        return max(0, int(os.environ.get("WITHDRAWAL_COOLDOWN_HOURS", "72")))
    except ValueError:
        return 72


def get_withdrawal_address_info(wallet_address: str) -> dict:
    """获取提币地址及冷却信息"""
    _ensure_db()
    wallet_address = wallet_address.lower()
    cooldown_h = withdrawal_cooldown_hours()

    with _connect() as conn:
        user = conn.execute(
            "SELECT metadata FROM users WHERE wallet_address = ?",
            (wallet_address,),
        ).fetchone()
        if not user or not user[0]:
            return {
                "withdrawal_address": None,
                "updated_at": None,
                "cooldown_hours": cooldown_h,
                "can_change": True,
                "cooldown_remaining_seconds": 0,
            }

        metadata = json.loads(user[0])
        addr = metadata.get("withdrawal_address")
        updated_at = metadata.get("withdrawal_address_updated_at")
        remaining = 0
        can_change = True
        if addr and updated_at and cooldown_h > 0:
            try:
                updated_dt = datetime.fromisoformat(updated_at)
                if updated_dt.tzinfo is None:
                    updated_dt = updated_dt.replace(tzinfo=TZ)
                elapsed = (datetime.now(TZ) - updated_dt).total_seconds()
                need = cooldown_h * 3600
                if elapsed < need:
                    can_change = False
                    remaining = int(need - elapsed)
            except Exception:
                pass

        return {
            "withdrawal_address": addr,
            "updated_at": updated_at,
            "cooldown_hours": cooldown_h,
            "can_change": can_change,
            "cooldown_remaining_seconds": remaining,
        }


def is_valid_withdrawal_address(address: str) -> bool:
    """与前端 points.html 一致：ERC20(0x+40) 或 TRC20(T+33)"""
    address = (address or "").strip()
    if address.startswith("0x") and len(address) == 42:
        return bool(re.match(r"^0x[0-9a-fA-F]{40}$", address))
    if address.startswith("T") and len(address) == 34:
        return bool(re.match(r"^T[1-9A-HJ-NP-Za-km-z]{33}$", address))
    return False


def bind_withdrawal_address(wallet_address: str, withdrawal_address: str) -> Tuple[bool, str]:
    """绑定或修改提币地址（含冷却期）"""
    _ensure_db()
    wallet_address = wallet_address.lower()
    withdrawal_address = (withdrawal_address or "").strip()
    if not is_valid_withdrawal_address(withdrawal_address):
        return False, "请输入有效的提币地址（ERC20: 0x 开头 42 位；TRC20: T 开头 34 位）"

    info = get_withdrawal_address_info(wallet_address)

    if info["withdrawal_address"] and not info["can_change"]:
        hours_left = max(1, info["cooldown_remaining_seconds"] // 3600)
        return False, f"提币地址修改冷却中，请 {hours_left} 小时后再试"

    with _connect() as conn:
        user = conn.execute(
            "SELECT metadata FROM users WHERE wallet_address = ?",
            (wallet_address,),
        ).fetchone()
        if not user:
            return False, "用户不存在"

        metadata = json.loads(user[0]) if user[0] else {}
        metadata["withdrawal_address"] = withdrawal_address.strip()
        metadata["withdrawal_address_updated_at"] = _now()

        conn.execute(
            "UPDATE users SET metadata = ? WHERE wallet_address = ?",
            (json.dumps(metadata, ensure_ascii=False), wallet_address),
        )
        conn.commit()
        return True, "提币地址绑定成功"


def get_withdrawal_address(wallet_address: str) -> Optional[str]:
    return get_withdrawal_address_info(wallet_address).get("withdrawal_address")


# ==================== 管理后台审计日志 ====================

def log_admin_audit(
    actor_wallet: str,
    action: str,
    target: Optional[str] = None,
    detail: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> None:
    _ensure_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO admin_audit_logs (actor_wallet, action, target, detail, ip_address, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (actor_wallet or "unknown").lower(),
                action,
                target,
                json.dumps(detail or {}, ensure_ascii=False),
                ip_address,
                _now(),
            ),
        )
        conn.commit()


def get_admin_audit_logs(limit: int = 100, action: Optional[str] = None) -> List[dict]:
    _ensure_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        if action:
            rows = conn.execute(
                """
                SELECT * FROM admin_audit_logs WHERE action = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (action, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM admin_audit_logs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


# ==================== 爆仓案例 ====================

def submit_liquidation_case(wallet_address: str, payload: dict) -> Tuple[bool, str, Optional[dict]]:
    _ensure_db()
    wallet_address = wallet_address.lower()

    exchange = (payload.get("exchange") or "").strip()
    symbol = (payload.get("symbol") or "").strip()
    direction = (payload.get("direction") or "").strip().lower()
    description = (payload.get("description") or "").strip()

    if not exchange or not symbol or not description:
        return False, "请填写交易所、交易对和案例描述", None
    if direction not in ("long", "short", "多", "空", "做多", "做空"):
        return False, "请选择爆仓方向（多/空）", None
    if len(description) < 20:
        return False, "案例描述至少 20 字", None

    dir_norm = "long" if direction in ("long", "多", "做多") else "short"
    can_do, msg = check_daily_limit(wallet_address, "submit_case", POINTS_RULES["submit_case"]["limit_per_day"])
    if not can_do:
        return False, msg, None

    evidence = payload.get("evidence_urls") or []
    if isinstance(evidence, str):
        evidence = [u.strip() for u in evidence.split(",") if u.strip()]

    now = _now()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            INSERT INTO liquidation_cases (
                wallet_address, exchange, symbol, timeframe, direction,
                event_time, price, loss_amount, description, evidence_urls,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                wallet_address,
                exchange,
                symbol,
                (payload.get("timeframe") or "").strip() or None,
                dir_norm,
                (payload.get("event_time") or "").strip() or None,
                payload.get("price"),
                payload.get("loss_amount"),
                description,
                json.dumps(evidence, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
        case_id = cursor.lastrowid
        case = conn.execute(
            "SELECT * FROM liquidation_cases WHERE id = ?",
            (case_id,),
        ).fetchone()

    add_points(wallet_address, "submit_case", metadata={"case_id": case_id})
    return True, "案例已提交，等待管理员审核", dict(case) if case else {"id": case_id}


def get_user_liquidation_cases(wallet_address: str, limit: int = 20) -> List[dict]:
    _ensure_db()
    wallet_address = wallet_address.lower()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM liquidation_cases
            WHERE wallet_address = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (wallet_address, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_liquidation_cases(limit: int = 50) -> List[dict]:
    _ensure_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM liquidation_cases
            WHERE status = 'pending'
            ORDER BY created_at ASC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def review_liquidation_case(
    case_id: int,
    approve: bool,
    reviewer_wallet: str,
    review_notes: str = "",
) -> dict:
    _ensure_db()
    reviewer_wallet = reviewer_wallet.lower()
    now = _now()

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        case = conn.execute(
            "SELECT * FROM liquidation_cases WHERE id = ?",
            (case_id,),
        ).fetchone()
        if not case:
            raise ValueError("案例不存在")
        if case["status"] != "pending":
            raise ValueError("案例已审核")

        status = "approved" if approve else "rejected"
        points_awarded = 0
        if approve:
            ok, _, pts = add_points(
                case["wallet_address"],
                "case_verified",
                metadata={"case_id": case_id, "reviewer": reviewer_wallet},
            )
            if ok:
                points_awarded = pts

        conn.execute(
            """
            UPDATE liquidation_cases
            SET status = ?, reviewer_wallet = ?, review_notes = ?,
                reviewed_at = ?, points_awarded = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, reviewer_wallet, review_notes, now, points_awarded, now, case_id),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM liquidation_cases WHERE id = ?",
            (case_id,),
        ).fetchone()
        return dict(updated)


# ==================== 积分兑换人工审核 ====================

def submit_exchange_redemption(wallet_address: str, points_amount: int) -> Tuple[bool, str, Optional[dict]]:
    _ensure_db()
    wallet_address = wallet_address.lower()

    rule = get_current_exchange_rule()
    if not rule:
        return False, "暂无兑换规则", None

    addr_info = get_withdrawal_address_info(wallet_address)
    if not addr_info.get("withdrawal_address"):
        return False, "请先绑定提币地址", None

    if points_amount < rule["min_amount"]:
        return False, f"最低兑换 {rule['min_amount']} 积分", None
    if points_amount > rule["max_amount"]:
        return False, f"单次最高兑换 {rule['max_amount']} 积分", None

    user = get_user(wallet_address)
    if not user:
        return False, "用户不存在", None
    if user["total_points"] < points_amount:
        return False, "积分不足", None

    pending = _count_redemption_points_today(wallet_address)
    if pending + points_amount > rule["daily_limit"]:
        return False, f"超过每日兑换限额 {rule['daily_limit']} 积分", None

    usdt_amount = round(points_amount / rule["exchange_rate"], 4)
    now = _now()

    ok, msg, _ = add_points(
        wallet_address,
        "exchange_redeem_hold",
        points=-points_amount,
        metadata={"pending_redemption": True},
    )
    if not ok:
        return False, msg, None

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            INSERT INTO exchange_redemption_requests (
                wallet_address, points_amount, usdt_amount, exchange_rate,
                withdrawal_address, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                wallet_address,
                points_amount,
                usdt_amount,
                rule["exchange_rate"],
                addr_info["withdrawal_address"],
                now,
                now,
            ),
        )
        conn.commit()
        req = conn.execute(
            "SELECT * FROM exchange_redemption_requests WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return True, "兑换申请已提交，等待人工审核", dict(req)


def _count_redemption_points_today(wallet_address: str) -> int:
    today_prefix = _today() + "%"
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(points_amount), 0) FROM exchange_redemption_requests
            WHERE wallet_address = ? AND created_at LIKE ?
              AND status IN ('pending', 'approved', 'paid')
            """,
            (wallet_address, today_prefix),
        ).fetchone()
        return int(row[0] or 0)


def get_user_redemption_requests(wallet_address: str, limit: int = 20) -> List[dict]:
    _ensure_db()
    wallet_address = wallet_address.lower()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM exchange_redemption_requests
            WHERE wallet_address = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (wallet_address, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_redemption_requests(limit: int = 50) -> List[dict]:
    _ensure_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM exchange_redemption_requests
            WHERE status = 'pending'
            ORDER BY created_at ASC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def review_redemption_request(
    request_id: int,
    approve: bool,
    reviewer_wallet: str,
    review_notes: str = "",
    txhash: Optional[str] = None,
) -> dict:
    _ensure_db()
    reviewer_wallet = reviewer_wallet.lower()
    now = _now()

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        req = conn.execute(
            "SELECT * FROM exchange_redemption_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if not req:
            raise ValueError("兑换申请不存在")
        if req["status"] != "pending":
            raise ValueError("申请已处理")

        if approve:
            status = "paid" if txhash else "approved"
            conn.execute(
                """
                UPDATE exchange_redemption_requests
                SET status = ?, reviewer_wallet = ?, review_notes = ?,
                    reviewed_at = ?, txhash = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, reviewer_wallet, review_notes, now, txhash, now, request_id),
            )
        else:
            add_points(
                req["wallet_address"],
                "exchange_redeem_refund",
                points=req["points_amount"],
                metadata={"request_id": request_id, "reason": review_notes},
            )
            conn.execute(
                """
                UPDATE exchange_redemption_requests
                SET status = 'rejected', reviewer_wallet = ?, review_notes = ?,
                    reviewed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (reviewer_wallet, review_notes, now, now, request_id),
            )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM exchange_redemption_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        return dict(updated)


def mark_redemption_paid(request_id: int, txhash: str, operator_wallet: str) -> dict:
    _ensure_db()
    now = _now()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        req = conn.execute(
            "SELECT * FROM exchange_redemption_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if not req:
            raise ValueError("兑换申请不存在")
        if req["status"] not in ("pending", "approved"):
            raise ValueError("当前状态不可标记已打款")

        conn.execute(
            """
            UPDATE exchange_redemption_requests
            SET status = 'paid', txhash = ?, reviewer_wallet = ?,
                reviewed_at = COALESCE(reviewed_at, ?), updated_at = ?
            WHERE id = ?
            """,
            (txhash, operator_wallet.lower(), now, now, request_id),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM exchange_redemption_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        return dict(updated)


if __name__ == "__main__":
    _ensure_db()
    init_default_exchange_rule()
    print("✅ 积分系统数据库初始化完成")
    print(f"数据库路径: {DB_PATH}")


def get_invite_stats(wallet_address: str) -> dict:
    """获取邀请统计"""
    _ensure_db()
    wallet_address = wallet_address.lower()
    
    with _connect() as conn:
        # 获取邀请码
        user = conn.execute(
            "SELECT invite_code FROM users WHERE wallet_address = ?",
            (wallet_address,)
        ).fetchone()
        
        if not user:
            return {"invite_count": 0, "invite_points": 0}
        
        invite_code = user[0]
        
        # 统计通过此邀请码完成注册并发放奖励的用户数
        invite_count = conn.execute(
            "SELECT COUNT(*) FROM referral_events WHERE inviter_wallet = ?",
            (wallet_address,)
        ).fetchone()[0]
        
        # 统计邀请获得的积分
        invite_points = conn.execute(
            """
            SELECT COALESCE(SUM(points_change), 0) 
            FROM points_history 
            WHERE wallet_address = ? AND action_type = 'invite_user'
            """,
            (wallet_address,)
        ).fetchone()[0]
        
        return {
            "invite_count": invite_count,
            "invite_points": invite_points
        }
