#!/usr/bin/env python3
"""
每日热门合约（按 24h 成交额）Top N 插针扫描，结果写入 SQLite。
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Callable, List, Optional, Tuple

import pandas as pd

from wick_detector_v4 import LiquidationDetector, get_default_amplitude

from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "wick_daily.db")

TZ_NAME = os.environ.get("DAILY_REPORT_TZ", "Asia/Shanghai")
TOP_N = int(os.environ.get("DAILY_TOP_N", "100"))
TIMEFRAME = os.environ.get("DAILY_TIMEFRAME", "5m")
WORKERS = max(1, min(8, int(os.environ.get("DAILY_SCAN_WORKERS", "4"))))
# 单所覆盖用（兼容旧环境）；多所用 DAILY_EXCHANGES
EXCHANGE_ID = os.environ.get("DAILY_HOT_EXCHANGE", "binanceusdm")


def _daily_exchange_ids() -> List[str]:
    raw = os.environ.get("DAILY_EXCHANGES", "binanceusdm,okx,gate,bybit,mexc,bitget")
    return [x.strip() for x in raw.split(",") if x.strip()]


EXCHANGE_LABELS = {
    "binanceusdm": "币安 USDM",
    "okx": "欧易",
    "gate": "Gate.io",
    "bybit": "Bybit",
    "mexc": "MEXC",
    "bitget": "Bitget",
}

_job_lock = threading.Lock()
_daily_scan_lock_fd = None
JOB_STATE: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "progress": "0/0",
    "last_error": None,
    "phase_exchange": None,
    "phase_index": None,
}


def _tz() -> ZoneInfo:
    return ZoneInfo(TZ_NAME)


def _acquire_daily_scan_lock() -> bool:
    """跨 gunicorn worker 的日报任务排他锁（Linux fcntl）。"""
    global _daily_scan_lock_fd
    path = os.path.join(BASE_DIR, "data", ".daily_scan.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        import fcntl
    except ImportError:
        return True
    fd = open(path, "a+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fd.close()
        return False
    _daily_scan_lock_fd = fd
    return True


def _release_daily_scan_lock() -> None:
    global _daily_scan_lock_fd
    if _daily_scan_lock_fd is None:
        return
    try:
        import fcntl

        fcntl.flock(_daily_scan_lock_fd.fileno(), fcntl.LOCK_UN)
        _daily_scan_lock_fd.close()
    except OSError:
        pass
    _daily_scan_lock_fd = None


def _ensure_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS daily_reports (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              report_date TEXT NOT NULL,
              exchange TEXT NOT NULL,
              generated_at TEXT NOT NULL,
              symbol_count INTEGER,
              with_hits_count INTEGER,
              UNIQUE(report_date, exchange)
            );
            CREATE TABLE IF NOT EXISTS daily_report_rows (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              report_id INTEGER NOT NULL,
              rank_vol INTEGER,
              symbol TEXT NOT NULL,
              quote_volume REAL,
              total_klines INTEGER,
              hit_count INTEGER,
              max_amplitude REAL,
              hits_json TEXT,
              FOREIGN KEY (report_id) REFERENCES daily_reports(id)
            );
            CREATE INDEX IF NOT EXISTS idx_rows_report ON daily_report_rows(report_id);
            """
        )


def _normalize_symbol(sym: str) -> str:
    return sym.split(":")[0] if ":" in sym else sym


def get_hot_symbols_usdm(exchange_id: str, limit: int) -> List[Tuple[str, float]]:
    import ccxt

    try:
        ex = getattr(ccxt, exchange_id)(
            {"enableRateLimit": True, "timeout": 30000, "options": {"defaultType": "swap"}}
        )
        ex.load_markets()
        tickers = ex.fetch_tickers()
    except Exception as e:
        raise RuntimeError(f"无法获取热门合约列表: {exchange_id} - {type(e).__name__}: {str(e)[:100]}")
    
    pairs: List[Tuple[str, float]] = []
    for sid, t in tickers.items():
        m = ex.markets.get(sid)
        if not m:
            continue
        # 支持永续合约和线性合约
        if not (m.get("swap") or m.get("linear")):
            continue
        # 支持 USDT、USD、USDC 计价
        quote = m.get("quote")
        if quote not in ("USDT", "USD", "USDC"):
            continue
        clean = _normalize_symbol(sid)
        # 接受 /USDT、/USD、/USDC 结尾
        if not (clean.endswith("/USDT") or clean.endswith("/USD") or clean.endswith("/USDC")):
            continue
        # 尝试多个字段：quoteVolume, baseVolume * last
        qv = 0.0
        if t.get("quoteVolume"):
            qv = float(t.get("quoteVolume") or 0)
        elif t.get("baseVolume") and t.get("last"):
            qv = float(t.get("baseVolume") or 0) * float(t.get("last") or 0)
        elif t.get("info") and isinstance(t.get("info"), dict):
            # 欧易可能在 info 里
            info = t.get("info")
            if info.get("volCcy24h"):
                qv = float(info.get("volCcy24h") or 0)
            elif info.get("vol24h") and info.get("last"):
                qv = float(info.get("vol24h") or 0) * float(info.get("last") or 0)
        if qv > 0:
            pairs.append((clean, qv))
    
    if not pairs:
        raise RuntimeError(f"无法获取热门合约列表: {exchange_id} - 没有找到符合条件的永续合约（USDT/USD/USDC）")
    
    pairs.sort(key=lambda x: -x[1])
    out: List[Tuple[str, float]] = []
    seen = set()
    for s, v in pairs:
        if s in seen:
            continue
        seen.add(s)
        out.append((s, v))
        if len(out) >= limit:
            break
    return out


def fetch_ohlcv_calendar_day(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    day: date,
    market_type: str = "swap",
) -> pd.DataFrame:
    import ccxt

    ex = getattr(ccxt, exchange_id)(
        {"enableRateLimit": True, "timeout": 30000, "options": {"defaultType": "swap"}}
    )
    ex.load_markets()
    tz = _tz()
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = start + timedelta(days=1)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    params: dict = {}
    if market_type == "swap":
        params["defaultType"] = "swap"

    sym = symbol
    if exchange_id == "okx" and ":USDT" not in sym:
        sym = sym + ":USDT"
    elif exchange_id == "bybit" and ":USDT" not in sym:
        sym = sym + ":USDT"

    chunks: list = []
    since = start_ms
    while since < end_ms:
        batch = ex.fetch_ohlcv(sym, timeframe, since=since, limit=1000, params=params)
        if not batch:
            break
        for c in batch:
            if c[0] < end_ms:
                chunks.append(c)
        since = batch[-1][0] + 1
        if len(batch) < 50:
            break

    if not chunks:
        return pd.DataFrame()
    df = pd.DataFrame(
        chunks, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def _scan_one(args: Tuple[int, str, float, date, str, str]) -> dict:
    rank_vol, symbol, quote_vol, target_day, exchange_id, tf = args
    row: dict = {
        "rank_vol": rank_vol,
        "symbol": symbol,
        "quote_volume": quote_vol,
        "total_klines": 0,
        "hit_count": 0,
        "max_amplitude": 0.0,
        "hits": [],
        "error": None,
    }
    try:
        df = fetch_ohlcv_calendar_day(exchange_id, symbol, tf, target_day)
        row["total_klines"] = len(df)
        if df.empty:
            return row
        det = LiquidationDetector(exchange_name=exchange_id, symbol=symbol)
        _ma = os.environ.get("DAILY_MIN_AMP")
        min_amp = (
            float(_ma) if _ma not in (None, "") else float(get_default_amplitude(symbol))
        )
        scored = det.detect_wicks(df, min_amplitude_pct=min_amp)
        if "amplitude" in scored.columns and len(scored):
            row["max_amplitude"] = round(float(scored["amplitude"].max()), 4)
        hits = scored[scored["wick_score"] >= 0.8]
        row["hit_count"] = int(len(hits))
        if len(hits):
            top = hits.nlargest(5, "amplitude")
            for _, r in top.iterrows():
                row["hits"].append(
                    {
                        "timestamp": str(r["timestamp"]),
                        "direction": str(r.get("direction", "")),
                        "amplitude": round(float(r["amplitude"]), 2),
                    }
                )
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
    return row


def run_daily_scan(
    report_date: Optional[date] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    exchange_id: Optional[str] = None,
) -> int:
    """
    扫描「report_date」当日（上海时区日历日）某交易所热门 TopN 合约插针情况，写入数据库。
    exchange_id 默认单所 EXCHANGE_ID（环境变量）。
    返回 report_id。
    """
    _ensure_db()
    tz = _tz()
    exid = (exchange_id or EXCHANGE_ID).strip()
    if report_date is None:
        report_date = (datetime.now(tz) - timedelta(days=1)).date()

    hot = get_hot_symbols_usdm(exid, TOP_N)
    if not hot:
        raise RuntimeError(f"无法获取热门合约列表: {exid}")

    total = len(hot)
    JOB_STATE["progress"] = f"0/{total}"
    results: List[dict] = []

    def _prog(cur: int) -> None:
        s = f"{cur}/{total}"
        JOB_STATE["progress"] = s
        if progress_cb:
            progress_cb(s)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = []
        for i, (sym, qv) in enumerate(hot, start=1):
            args = (i, sym, qv, report_date, exid, TIMEFRAME)
            futs.append(pool.submit(_scan_one, args))
        done = 0
        for fut in as_completed(futs):
            results.append(fut.result())
            done += 1
            _prog(done)

    with_hits = sum(1 for r in results if r.get("hit_count", 0) > 0)
    generated_at = datetime.now(tz).isoformat(timespec="seconds")

    with sqlite3.connect(DB_PATH) as conn:
        old = conn.execute(
            "SELECT id FROM daily_reports WHERE report_date = ? AND exchange = ?",
            (report_date.isoformat(), exid),
        ).fetchone()
        if old:
            conn.execute(
                "DELETE FROM daily_report_rows WHERE report_id = ?", (old[0],)
            )
            conn.execute(
                "DELETE FROM daily_reports WHERE id = ?", (old[0],)
            )
        cur = conn.execute(
            """INSERT INTO daily_reports
               (report_date, exchange, generated_at, symbol_count, with_hits_count)
               VALUES (?,?,?,?,?)""",
            (
                report_date.isoformat(),
                exid,
                generated_at,
                len(results),
                with_hits,
            ),
        )
        rid = cur.lastrowid
        for r in sorted(results, key=lambda x: x["rank_vol"]):
            conn.execute(
                """INSERT INTO daily_report_rows
                   (report_id, rank_vol, symbol, quote_volume, total_klines,
                    hit_count, max_amplitude, hits_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    rid,
                    r["rank_vol"],
                    r["symbol"],
                    r["quote_volume"],
                    r["total_klines"],
                    r["hit_count"],
                    r["max_amplitude"],
                    json.dumps(
                        {"hits": r.get("hits") or [], "error": r.get("error")},
                        ensure_ascii=False,
                    ),
                ),
            )
        conn.commit()
    _write_daily_json_file(
        report_date, exid, generated_at, results, with_hits
    )
    return int(rid)


def _rows_for_export(results: List[dict]) -> List[dict]:
    out: List[dict] = []
    for r in sorted(results, key=lambda x: x["rank_vol"]):
        out.append(
            {
                "rank_vol": r["rank_vol"],
                "symbol": r["symbol"],
                "quote_volume": r["quote_volume"],
                "total_klines": r["total_klines"],
                "hit_count": r["hit_count"],
                "max_amplitude": r["max_amplitude"],
                "hits": r.get("hits") or [],
                "error": r.get("error"),
            }
        )
    return out


def _write_daily_json_file(
    report_date: date,
    exid: str,
    generated_at: str,
    results: List[dict],
    with_hits: int,
) -> str:
    """按日期落盘：data/reports/YYYY-MM-DD/{exchange}.json"""
    if os.environ.get("DISABLE_DAILY_JSON_EXPORT") == "1":
        return ""
    day_dir = os.path.join(BASE_DIR, "data", "reports", report_date.isoformat())
    os.makedirs(day_dir, exist_ok=True)
    payload = {
        "report_date": report_date.isoformat(),
        "exchange": exid,
        "generated_at": generated_at,
        "symbol_count": len(results),
        "with_hits_count": with_hits,
        "rows": _rows_for_export(results),
    }
    path = os.path.join(day_dir, f"{exid}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def _git_log_path() -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, "data", "logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "git_auto_commit.log")


def _git_append_log(line: str) -> None:
    ts = datetime.now(_tz()).strftime("%Y-%m-%d %H:%M:%S")
    msg = f"[{ts}] {line}\n"
    print(f"[git] {line}", flush=True)
    try:
        with open(_git_log_path(), "a", encoding="utf-8") as f:
            f.write(msg)
    except OSError:
        pass


def _git_run(cmd: list, cwd: str, timeout: int = 120):
    env = os.environ.copy()
    # systemd 下 gunicorn 子进程可能没有 HOME，导致 git/ssh 读不到 ~/.ssh
    if not env.get("HOME"):
        env["HOME"] = os.path.expanduser("~") or "/root"
    name = env.get("GIT_AUTHOR_NAME", "wickdetector-bot")
    email = env.get("GIT_AUTHOR_EMAIL", "bot@wickdetector.com")
    env.setdefault("GIT_AUTHOR_NAME", name)
    env.setdefault("GIT_AUTHOR_EMAIL", email)
    env.setdefault("GIT_COMMITTER_NAME", name)
    env.setdefault("GIT_COMMITTER_EMAIL", email)
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _git_auto_commit(report_date: date) -> None:
    """自动提交日报数据并推送到远程（失败详情见 data/logs/git_auto_commit.log）"""
    if os.environ.get("DISABLE_GIT_AUTO_PUSH", "").lower() in ("1", "true", "yes"):
        _git_append_log("已禁用 (DISABLE_GIT_AUTO_PUSH=1)")
        return

    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        date_str = report_date.strftime("%Y-%m-%d")
        remote = os.environ.get("GIT_PUSH_REMOTE", "origin")
        branch = os.environ.get("GIT_PUSH_BRANCH", "").strip()

        result = _git_run(["git", "rev-parse", "--git-dir"], base_dir, timeout=15)
        if result.returncode != 0:
            _git_append_log("不是 Git 仓库，跳过")
            return

        if not branch:
            br = _git_run(["git", "rev-parse", "--abbrev-ref", "HEAD"], base_dir, timeout=15)
            branch = (br.stdout or "").strip() or "main"

        paths_to_add = []
        for rel in ("data/reports", "data/wick_daily.db"):
            full = os.path.join(base_dir, rel)
            if os.path.exists(full):
                paths_to_add.append(rel)
        if not paths_to_add:
            _git_append_log("无 data/reports 或 data/wick_daily.db，跳过")
            return

        add = _git_run(["git", "add", "--"] + paths_to_add, base_dir, timeout=60)
        if add.returncode != 0:
            _git_append_log(f"git add 失败: {(add.stderr or add.stdout or '').strip()}")
            return

        diff = _git_run(["git", "diff", "--cached", "--quiet"], base_dir, timeout=15)
        if diff.returncode == 0:
            _git_append_log(f"没有数据变更，跳过提交 ({date_str})")
            return

        commit_msg = f"chore: update daily reports for {date_str}"
        commit = _git_run(
            [
                "git",
                "-c",
                f"user.name={os.environ.get('GIT_AUTHOR_NAME', 'wickdetector-bot')}",
                "-c",
                f"user.email={os.environ.get('GIT_AUTHOR_EMAIL', 'bot@wickdetector.com')}",
                "commit",
                "-m",
                commit_msg,
            ],
            base_dir,
            timeout=60,
        )
        if commit.returncode != 0:
            err = (commit.stderr or commit.stdout or "").strip()
            _git_append_log(f"git commit 失败: {err}")
            return

        pull = _git_run(
            ["git", "pull", "--rebase", "--autostash", remote, branch],
            base_dir,
            timeout=120,
        )
        if pull.returncode != 0:
            err = (pull.stderr or pull.stdout or "").strip()
            _git_append_log(f"git pull --rebase 失败（请先手动解决冲突）: {err}")
            return

        push = _git_run(["git", "push", remote, branch], base_dir, timeout=120)
        if push.returncode == 0:
            _git_append_log(f"成功提交并推送 {date_str} -> {remote}/{branch}")
        else:
            err = (push.stderr or push.stdout or "").strip()
            _git_append_log(f"git push 失败: {err}")
            _git_append_log(
                "提示: 在服务器执行 git config user.name/user.email；"
                "SSH 用 ssh -T git@github.com 测试；HTTPS 需配置 token"
            )

    except subprocess.TimeoutExpired:
        _git_append_log("Git 操作超时")
    except Exception as e:
        _git_append_log(f"异常: {e}")


def job_worker(report_date: Optional[date] = None) -> None:
    with _job_lock:
        if JOB_STATE["running"]:
            return
        JOB_STATE["running"] = True
        JOB_STATE["last_error"] = None
        JOB_STATE["started_at"] = datetime.now(_tz()).isoformat(timespec="seconds")
        JOB_STATE["progress"] = "0/0"
        JOB_STATE["phase_exchange"] = None
        JOB_STATE["phase_index"] = None
    exlist = _daily_exchange_ids()
    actual_report_date = report_date or (date.today() - timedelta(days=1))
    try:
        errs: List[str] = []
        for i, exid in enumerate(exlist):
            JOB_STATE["phase_exchange"] = exid
            JOB_STATE["phase_index"] = f"{i + 1}/{len(exlist)}"
            try:
                run_daily_scan(report_date=report_date, exchange_id=exid)
            except Exception:
                errs.append(f"{exid}: {traceback.format_exc()}")
                JOB_STATE["last_error"] = "\n---\n".join(errs)
        
        # 所有交易所扫描完成后，自动提交到 GitHub
        if not errs or len(errs) < len(exlist):  # 至少有一个成功
            _git_auto_commit(actual_report_date)
            
    except Exception:
        JOB_STATE["last_error"] = traceback.format_exc()
    finally:
        _release_daily_scan_lock()
        with _job_lock:
            JOB_STATE["running"] = False
            JOB_STATE["phase_exchange"] = None
            JOB_STATE["phase_index"] = None
            JOB_STATE["progress"] = JOB_STATE.get("progress", "done")


def start_daily_job_async(report_date: Optional[date] = None) -> bool:
    """若当前无任务在跑则启动后台线程。返回是否已启动。"""
    if not _acquire_daily_scan_lock():
        return False
    with _job_lock:
        if JOB_STATE["running"]:
            _release_daily_scan_lock()
            return False
    t = threading.Thread(target=job_worker, args=(report_date,), daemon=True)
    t.start()
    return True


def _report_rows_from_db_row(conn: sqlite3.Connection, rid: int) -> List[dict]:
    rows = conn.execute(
        """SELECT rank_vol, symbol, quote_volume, total_klines, hit_count,
                  max_amplitude, hits_json
           FROM daily_report_rows WHERE report_id = ?
           ORDER BY rank_vol ASC""",
        (rid,),
    ).fetchall()
    out_rows: List[dict] = []
    for r in rows:
        hj = json.loads(r["hits_json"] or "{}")
        out_rows.append(
            {
                "rank_vol": r["rank_vol"],
                "symbol": r["symbol"],
                "quote_volume": r["quote_volume"],
                "total_klines": r["total_klines"],
                "hit_count": r["hit_count"],
                "max_amplitude": r["max_amplitude"],
                "hits": hj.get("hits") or [],
                "error": hj.get("error"),
            }
        )
    return out_rows


def get_latest_report_for(exchange_id: str) -> Optional[dict]:
    _ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT * FROM daily_reports
               WHERE exchange = ?
               ORDER BY report_date DESC LIMIT 1""",
            (exchange_id,),
        ).fetchone()
        if not row:
            return None
        rid = row["id"]
        out_rows = _report_rows_from_db_row(conn, rid)
    return {
        "report_date": row["report_date"],
        "exchange": row["exchange"],
        "generated_at": row["generated_at"],
        "symbol_count": row["symbol_count"],
        "with_hits_count": row["with_hits_count"],
        "rows": out_rows,
    }


def get_latest_report() -> Optional[dict]:
    """兼容旧接口：返回默认第一所（binanceusdm）最新日报"""
    first = _daily_exchange_ids()[0] if _daily_exchange_ids() else EXCHANGE_ID
    return get_latest_report_for(first)


def get_all_latest_reports() -> dict[str, Optional[dict]]:
    out: dict[str, Optional[dict]] = {}
    for exid in _daily_exchange_ids():
        out[exid] = get_latest_report_for(exid)
    return out


def list_report_dates(limit: int = 30) -> List[str]:
    _ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """SELECT DISTINCT report_date FROM daily_reports
               ORDER BY report_date DESC LIMIT ?""",
            (limit,),
        )
        return [r[0] for r in cur.fetchall()]


def get_report_by_date(report_date: str, exchange_id: Optional[str] = None) -> Optional[dict]:
    _ensure_db()
    exid = exchange_id or EXCHANGE_ID
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT * FROM daily_reports WHERE report_date = ? AND exchange = ?""",
            (report_date, exid),
        ).fetchone()
        if not row:
            return None
        rid = row["id"]
        out_rows = _report_rows_from_db_row(conn, rid)
    return {
        "report_date": row["report_date"],
        "exchange": row["exchange"],
        "generated_at": row["generated_at"],
        "symbol_count": row["symbol_count"],
        "with_hits_count": row["with_hits_count"],
        "rows": out_rows,
    }


def get_job_state() -> dict:
    with _job_lock:
        ex = JOB_STATE.get("phase_exchange")
        return {
            "running": JOB_STATE["running"],
            "started_at": JOB_STATE.get("started_at"),
            "progress": JOB_STATE.get("progress"),
            "last_error": JOB_STATE.get("last_error"),
            "phase_exchange": ex,
            "phase_index": JOB_STATE.get("phase_index"),
            "phase_label": EXCHANGE_LABELS.get(ex, ex) if ex else None,
        }
