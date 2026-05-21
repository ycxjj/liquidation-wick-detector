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
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Callable, List, Optional, Tuple

import pandas as pd

from wick_detector_v4 import (
    LiquidationDetector,
    fetch_ohlcv_calendar_day_rest,
    get_default_amplitude,
    _timeframe_to_ms,
)

from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "wick_daily.db")

TZ_NAME = os.environ.get("DAILY_REPORT_TZ", "Asia/Shanghai")
# 0 表示扫描交易所返回的全部符合条件的永续合约；页面仍只展示前 100。
TOP_N = int(os.environ.get("DAILY_TOP_N", "0"))
TIMEFRAME = os.environ.get("DAILY_TIMEFRAME", "1m")
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
_scan_exchange_local = threading.local()

# gate / bitget / mexc 历史 1m 常不足整日，自动降级 5m
_OLD_1M_FALLBACK_5M = os.environ.get(
    "DAILY_OLD_1M_FALLBACK_5M",
    os.environ.get("DAILY_GATE_OLD_1M_FALLBACK_5M", "1"),
).lower() in ("1", "true", "yes")
_MIN_KLINE_RATIO = float(os.environ.get("DAILY_MIN_KLINE_RATIO", "0.35"))
# Gate 5m 整日约 288 根；低于该比例视为不完整并尝试第二数据源
_GATE_MIN_KLINE_RATIO = float(os.environ.get("DAILY_GATE_MIN_KLINE_RATIO", "0.88"))
# Gate 官方 5m 历史 K 线约仅保留最近 N 天（实测约 30～32 天，4/1～4/19 无法补全）
GATE_HISTORY_DAYS = int(os.environ.get("DAILY_GATE_HISTORY_DAYS", "32"))
_REST_CALENDAR_EXCHANGES = frozenset({"bitget", "mexc"})
_TF_FALLBACK_EXCHANGES = frozenset({"gate", "bitget", "mexc"})
_EXPECTED_KLINES = {"1m": 1440, "5m": 288, "15m": 96, "1h": 24}
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


def gate_history_earliest_date() -> date:
    """Gate 5m 可查询的最早日历日（含）。"""
    return datetime.now(_tz()).date() - timedelta(days=GATE_HISTORY_DAYS)


def gate_supports_report_date(report_date: date) -> bool:
    return report_date >= gate_history_earliest_date()


def gate_history_unavailable_message(report_date: date) -> str:
    earliest = gate_history_earliest_date()
    return (
        f"Gate 官方 5m K 线历史约仅保留最近 {GATE_HISTORY_DAYS} 天。"
        f"{report_date.isoformat()} 早于可查询范围（最早约 {earliest.isoformat()}），"
        f"无法从接口获取整日数据，不是扫描脚本故障。"
        f"请查看 {earliest.isoformat()} 及之后的日期。"
    )


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


def _get_scan_exchange(exchange_id: str):
    """线程内复用 ccxt 实例，避免每个合约 load_markets 触发限频。"""
    cache = getattr(_scan_exchange_local, "by_id", None)
    if cache is None:
        cache = {}
        _scan_exchange_local.by_id = cache
    if exchange_id not in cache:
        import ccxt

        opts: dict = {"defaultType": "swap"}
        if exchange_id == "gate":
            opts["defaultSettle"] = "usdt"
        ex = getattr(ccxt, exchange_id)(
            {"enableRateLimit": True, "timeout": 30000, "options": opts}
        )
        ex.load_markets()
        cache[exchange_id] = ex
    return cache[exchange_id]


def _expected_klines(timeframe: str) -> int:
    return _EXPECTED_KLINES.get(timeframe, 288)


def _min_kline_ratio(exchange_id: str = "") -> float:
    if (exchange_id or "").lower() == "gate":
        return _GATE_MIN_KLINE_RATIO
    return _MIN_KLINE_RATIO


def _insufficient_klines(
    df: pd.DataFrame, timeframe: str, exchange_id: str = ""
) -> bool:
    if df is None or df.empty:
        return True
    ratio = _min_kline_ratio(exchange_id)
    return len(df) < int(_expected_klines(timeframe) * ratio)


def _merge_ohlcv_dataframes(*parts: pd.DataFrame) -> pd.DataFrame:
    frames = [p for p in parts if p is not None and not p.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if "timestamp" not in df.columns:
        return df
    return (
        df.drop_duplicates(subset=["timestamp"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


def _normalize_symbol(sym: str) -> str:
    return sym.split(":")[0] if ":" in sym else sym


def _ohlcv_symbol_candidates(exchange, exchange_id: str, symbol: str) -> List[str]:
    """Return exchange-compatible OHLCV symbols for swap markets.

    Public report rows use clean symbols like BTC/USDT, but several ccxt swap
    markets (Gate/OKX/Bybit and sometimes others) require BTC/USDT:USDT when
    fetching OHLCV. Try exact market keys first, then fall back to matching by
    base/quote so the report can keep the clean display symbol.
    """
    candidates: List[str] = []

    def add(value: Optional[str]) -> None:
        if value and value not in candidates:
            candidates.append(value)

    add(symbol)

    if "/" in symbol and ":" not in symbol:
        base, quote = symbol.split("/", 1)
        quote = quote.split(":")[0]
        add(f"{base}/{quote}:{quote}")

    for key, market in exchange.markets.items():
        if _normalize_symbol(key) == symbol and (market.get("swap") or market.get("linear")):
            add(key)
            add(market.get("symbol"))

    # Some exchanges expose only clean symbols, while others expose settled
    # symbols. Keep both orders to support binanceusdm and Gate/OKX/Bybit.
    if exchange_id in ("gate", "okx", "bybit", "bitget", "mexc"):
        for key, market in exchange.markets.items():
            if not (market.get("swap") or market.get("linear")):
                continue
            if market.get("symbol") and _normalize_symbol(market["symbol"]) == symbol:
                add(market["symbol"])
            if market.get("base") and market.get("quote"):
                clean = f"{market['base']}/{market['quote']}"
                if clean == symbol:
                    add(market.get("symbol"))
                    add(key)

    return candidates


def _market_is_tradable_swap(market: Optional[dict]) -> bool:
    if not market:
        return False
    if not (market.get("swap") or market.get("linear")):
        return False
    if market.get("active") is False:
        return False
    return True


def _market_listing_ms(market: Optional[dict]) -> Optional[int]:
    """合约上线时间（毫秒），用于历史日报过滤当时尚未上市的币。"""
    if not market:
        return None
    for key in ("created", "listed", "listing"):
        v = market.get(key)
        if v is not None:
            try:
                t = int(v)
                return t if t >= 10**12 else t * 1000
            except (TypeError, ValueError):
                pass
    info = market.get("info")
    if isinstance(info, dict):
        for key in ("create_time", "launch_time", "create_time_ms", "in_delisting_time"):
            v = info.get(key)
            if v is not None and v != "":
                try:
                    t = int(float(v))
                    return t if t >= 10**12 else t * 1000
                except (TypeError, ValueError):
                    pass
    return None


def _market_listed_before_day(
    exchange, exchange_id: str, clean_symbol: str, day: date
) -> bool:
    """该合约在 day 当日结束前是否已上线（无法判断时保留）。"""
    tz = _tz()
    day_end_ms = int(
        datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz).timestamp()
        * 1000
    )
    for sym in _ohlcv_symbol_candidates(exchange, exchange_id, clean_symbol):
        m = exchange.markets.get(sym)
        if not _market_is_tradable_swap(m):
            continue
        listed_ms = _market_listing_ms(m)
        if listed_ms is None:
            return True
        return listed_ms < day_end_ms
    return True


def resolve_swap_ohlcv_symbol(exchange, exchange_id: str, symbol: str) -> Optional[str]:
    """Return first ccxt market id that can fetch swap OHLCV, else None."""
    for sym in _ohlcv_symbol_candidates(exchange, exchange_id, symbol):
        market = exchange.markets.get(sym)
        if _market_is_tradable_swap(market):
            return sym
    return None


def get_hot_symbols_usdm(
    exchange_id: str,
    limit: int,
    report_date: Optional[date] = None,
) -> List[Tuple[str, float]]:
    import ccxt

    exid = exchange_id.lower()
    try:
        if exid == "gate":
            ex = _get_scan_exchange("gate")
        else:
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
        if resolve_swap_ohlcv_symbol(ex, exchange_id, clean) is None:
            continue
        if report_date is not None and not _market_listed_before_day(
            ex, exchange_id, clean, report_date
        ):
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
        if limit > 0 and len(out) >= limit:
            break
    return out


def fetch_ohlcv_calendar_day(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    day: date,
    market_type: str = "swap",
    ex: Optional[Any] = None,
) -> pd.DataFrame:
    if ex is None:
        ex = _get_scan_exchange(exchange_id)
    tz = _tz()
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = start + timedelta(days=1)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    params: dict = {}
    if market_type == "swap":
        params["defaultType"] = "swap"
    if exchange_id == "gate":
        params["settle"] = "usdt"

    valid_symbols = [
        sym
        for sym in _ohlcv_symbol_candidates(ex, exchange_id, symbol)
        if _market_is_tradable_swap(ex.markets.get(sym))
    ]
    if not valid_symbols:
        return pd.DataFrame()

    timeframe_ms = _timeframe_to_ms(timeframe) or 60_000
    gate_pacing = exchange_id == "gate"
    chunks: list = []
    last_error: Optional[Exception] = None
    for sym in valid_symbols:
        chunks = []
        since = start_ms
        try:
            while since < end_ms:
                batch = None
                for attempt in range(4 if gate_pacing else 2):
                    try:
                        batch = ex.fetch_ohlcv(
                            sym, timeframe, since=since, limit=1000, params=params
                        )
                        break
                    except Exception as e:
                        last_error = e
                        err = str(e).lower()
                        if gate_pacing and any(
                            x in err
                            for x in ("rate", "429", "too many", "limit", "timeout")
                        ):
                            time.sleep(0.35 * (attempt + 1))
                            continue
                        raise
                if not batch:
                    break
                for c in batch:
                    if start_ms <= c[0] < end_ms:
                        chunks.append(c)
                last_ts = batch[-1][0]
                since = last_ts + timeframe_ms
                if gate_pacing:
                    time.sleep(0.08)
                if last_ts + timeframe_ms >= end_ms:
                    break
            if chunks:
                break
        except Exception as e:
            last_error = e
            if gate_pacing:
                time.sleep(0.2)
            continue

    if not chunks and last_error is not None:
        raise last_error

    if not chunks:
        return pd.DataFrame()
    df = pd.DataFrame(
        chunks, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def _gate_rest_contract(symbol: str) -> str:
    """Gate REST 合约名优先用 ccxt market id（如 BTC_USDT）。"""
    try:
        ex = _get_scan_exchange("gate")
        for sym in _ohlcv_symbol_candidates(ex, "gate", symbol):
            m = ex.markets.get(sym)
            if not m:
                continue
            cid = m.get("id") or (m.get("info") or {}).get("name")
            if cid and isinstance(cid, str) and "_" in cid:
                return cid.upper()
    except Exception:
        pass
    return _normalize_symbol(symbol).replace("/", "_").upper()


def _fetch_gate_calendar_day(
    symbol: str, timeframe: str, target_day: date
) -> pd.DataFrame:
    """Gate：ccxt + REST 双通道拉取并合并，提高整日 K 线完整度。"""
    df_ccxt = pd.DataFrame()
    for attempt in range(3):
        try:
            df_ccxt = fetch_ohlcv_calendar_day(
                "gate", symbol, timeframe, target_day
            )
            if not _insufficient_klines(df_ccxt, timeframe, "gate"):
                break
        except Exception:
            df_ccxt = pd.DataFrame()
        time.sleep(0.25 * (attempt + 1))

    df_rest = pd.DataFrame()
    try:
        df_rest = fetch_ohlcv_calendar_day_rest(
            "gate",
            symbol,
            timeframe,
            target_day,
            TZ_NAME,
            contract=_gate_rest_contract(symbol),
        )
    except Exception:
        pass

    return _merge_ohlcv_dataframes(df_ccxt, df_rest)


def _fetch_calendar_primary(
    exchange_id: str, symbol: str, timeframe: str, target_day: date
) -> pd.DataFrame:
    exid = exchange_id.lower()
    if exid == "gate":
        return _fetch_gate_calendar_day(symbol, timeframe, target_day)
    if exid in _REST_CALENDAR_EXCHANGES:
        df = fetch_ohlcv_calendar_day_rest(
            exid, symbol, timeframe, target_day, TZ_NAME
        )
        if df.empty:
            try:
                df_ccxt = fetch_ohlcv_calendar_day(
                    exid, symbol, timeframe, target_day
                )
                if len(df_ccxt) > len(df):
                    return df_ccxt
            except Exception:
                pass
        return df
    return fetch_ohlcv_calendar_day(exid, symbol, timeframe, target_day)


def _fetch_ohlcv_with_tf_fallback(
    exchange_id: str, symbol: str, timeframe: str, target_day: date
) -> tuple[pd.DataFrame, str]:
    """拉取日历日 K 线；gate/bitget/mexc 历史 1m 不足时自动降级 5m。"""
    exid = exchange_id.lower()
    should_fallback = (
        _OLD_1M_FALLBACK_5M and exid in _TF_FALLBACK_EXCHANGES and timeframe == "1m"
    )
    try:
        df = _fetch_calendar_primary(exid, symbol, timeframe, target_day)
    except Exception:
        if not should_fallback:
            raise
        df = pd.DataFrame()

    if should_fallback and _insufficient_klines(df, "1m", exid):
        df5 = _fetch_calendar_primary(exid, symbol, "5m", target_day)
        if not df5.empty and (df.empty or len(df5) >= len(df)):
            return df5, "5m"
    return df, timeframe


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
        "timeframe": tf,
    }
    try:
        df, used_tf = _fetch_ohlcv_with_tf_fallback(exchange_id, symbol, tf, target_day)
        row["timeframe"] = used_tf
        row["total_klines"] = len(df)
        if df.empty:
            row["error"] = (
                f"未取到 {target_day.isoformat()} 当日K线"
                f"（当时未上市、已下架，或 Gate 历史接口无数据）"
            )
            return row
        exp = _expected_klines(used_tf)
        min_need = int(exp * _min_kline_ratio(exchange_id))
        if exchange_id.lower() == "gate" and len(df) < min_need:
            row["error"] = (
                f"K线不完整 {len(df)}/{exp}（Gate 目标≥{min_need} 根）"
            )
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
        msg = str(e)
        if "does not have market symbol" in msg or type(e).__name__ in (
            "BadSymbol",
            "BadRequest",
        ):
            row["error"] = f"合约不存在或已下架: {symbol}"
        else:
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

    if exid.lower() == "gate" and not gate_supports_report_date(report_date):
        return _write_gate_history_unavailable_report(report_date)

    sym_limit = TOP_N
    if exid.lower() == "gate" and report_date:
        days_ago = (datetime.now(_tz()).date() - report_date).days
        if days_ago > 2:
            cap = int(os.environ.get("DAILY_GATE_HISTORICAL_TOP_N", "400"))
            sym_limit = cap if TOP_N <= 0 else min(TOP_N, cap)

    hot = get_hot_symbols_usdm(exid, sym_limit, report_date=report_date)
    if not hot:
        raise RuntimeError(f"无法获取热门合约列表: {exid}")

    # 预热 ccxt（Gate 也依赖 ccxt 拉历史 K 线）
    if exid.lower() == "gate" or exid.lower() not in _REST_CALENDAR_EXCHANGES:
        _get_scan_exchange(exid)

    total = len(hot)
    JOB_STATE["progress"] = f"0/{total}"
    results: List[dict] = []

    def _prog(cur: int) -> None:
        s = f"{cur}/{total}"
        JOB_STATE["progress"] = s
        if progress_cb:
            progress_cb(s)

    scan_workers = WORKERS
    if exid.lower() == "gate":
        scan_workers = max(
            1,
            min(3, int(os.environ.get("DAILY_GATE_SCAN_WORKERS", "2"))),
        )

    with ThreadPoolExecutor(max_workers=scan_workers) as pool:
        futs = []
        for i, (sym, qv) in enumerate(hot, start=1):
            args = (i, sym, qv, report_date, exid, TIMEFRAME)
            futs.append(pool.submit(_scan_one, args))
        done = 0
        for fut in as_completed(futs):
            results.append(fut.result())
            done += 1
            _prog(done)

    def _row_counts_as_success(row: dict) -> bool:
        k = int(row.get("total_klines") or 0)
        if k <= 0:
            return False
        err = str(row.get("error") or "")
        if err.startswith("K线不完整"):
            return True
        return not err

    successful_rows = [r for r in results if _row_counts_as_success(r)]
    min_success = max(1, min(10, len(results) // 20))
    if not successful_rows or len(successful_rows) < min_success:
        raise RuntimeError(
            f"{exid} {report_date.isoformat()} 扫描有效数据过少 "
            f"({len(successful_rows)}/{len(results)})，已停止写库以避免覆盖旧日报"
        )

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
                        {
                            "hits": r.get("hits") or [],
                            "error": r.get("error"),
                            "timeframe": r.get("timeframe"),
                        },
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
                "timeframe": r.get("timeframe"),
            }
        )
    return out


def _write_gate_history_unavailable_report(report_date: date) -> int:
    """超出 Gate 历史窗口的日期：写入说明型空报告，避免满屏「未取到K线」。"""
    tz = _tz()
    exid = "gate"
    generated_at = datetime.now(tz).isoformat(timespec="seconds")
    msg = gate_history_unavailable_message(report_date)
    results: List[dict] = []
    _ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        old = conn.execute(
            "SELECT id FROM daily_reports WHERE report_date = ? AND exchange = ?",
            (report_date.isoformat(), exid),
        ).fetchone()
        if old:
            conn.execute(
                "DELETE FROM daily_report_rows WHERE report_id = ?", (old[0],)
            )
            conn.execute("DELETE FROM daily_reports WHERE id = ?", (old[0],))
        cur = conn.execute(
            """INSERT INTO daily_reports
               (report_date, exchange, generated_at, symbol_count, with_hits_count)
               VALUES (?,?,?,?,?)""",
            (report_date.isoformat(), exid, generated_at, 0, 0),
        )
        rid = int(cur.lastrowid)
        conn.commit()
    day_dir = os.path.join(BASE_DIR, "data", "reports", report_date.isoformat())
    os.makedirs(day_dir, exist_ok=True)
    payload = {
        "report_date": report_date.isoformat(),
        "exchange": exid,
        "generated_at": generated_at,
        "timeframe": TIMEFRAME,
        "symbol_count": 0,
        "with_hits_count": 0,
        "rows": [],
        "gate_history_unavailable": True,
        "gate_history_message": msg,
        "gate_earliest_date": gate_history_earliest_date().isoformat(),
        "gate_history_days": GATE_HISTORY_DAYS,
    }
    path = os.path.join(day_dir, "gate.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return rid


def _write_daily_json_file(
    report_date: date,
    exid: str,
    generated_at: str,
    results: List[dict],
    with_hits: int,
    extra_meta: Optional[dict] = None,
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
        "timeframe": TIMEFRAME,
        "symbol_count": len(results),
        "with_hits_count": with_hits,
        "rows": _rows_for_export(results),
    }
    if extra_meta:
        payload.update(extra_meta)
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


def _refresh_wickshield_rates_after_daily_reports() -> None:
    """日报六所扫描结束后，由日报数据重建精算费率表。"""
    if os.environ.get("DISABLE_RATE_TABLE_REFRESH", "").strip() in ("1", "true", "yes"):
        print("[wickshield] 费率表刷新已跳过 (DISABLE_RATE_TABLE_REFRESH)", flush=True)
        return
    try:
        from scripts.wickshield.rate_table import refresh_rates_after_daily_reports

        result = refresh_rates_after_daily_reports()
        if result.get("success"):
            print(
                f"[wickshield] 费率表已更新: {result.get('symbol_count')} 币种 -> data/rates/dynamic_rates.json",
                flush=True,
            )
        else:
            print(f"[wickshield] 费率表刷新失败: {result.get('error', result)}", flush=True)
    except Exception as e:
        print(f"[wickshield] 费率表刷新异常: {e}", flush=True)


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
        
        # 六所日报落盘后：刷新 WickShield 动态费率表（data/rates/dynamic_rates.json）
        if not errs or len(errs) < len(exlist):
            _refresh_wickshield_rates_after_daily_reports()

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
                "timeframe": hj.get("timeframe"),
            }
        )
    return out_rows


def _infer_report_timeframe(rows: List[dict]) -> Optional[str]:
    seen = sorted({r.get("timeframe") for r in rows if r.get("timeframe")})
    if not seen:
        return None
    if len(seen) == 1:
        return seen[0]
    return "mixed(" + ",".join(seen) + ")"


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
        "timeframe": _infer_report_timeframe(out_rows),
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


def _list_report_dates_from_disk() -> List[str]:
    """data/reports/YYYY-MM-DD 目录名（有任意 json 即算有存档）。"""
    root = os.path.join(BASE_DIR, "data", "reports")
    if not os.path.isdir(root):
        return []
    out: List[str] = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if not os.path.isdir(path) or len(name) != 10:
            continue
        try:
            date.fromisoformat(name)
        except ValueError:
            continue
        if any(f.endswith(".json") for f in os.listdir(path)):
            out.append(name)
    return out


def list_report_dates(limit: Optional[int] = None) -> List[str]:
    """合并库内日期与磁盘 JSON 日期，供页面日历选择。limit=0 表示不截断。"""
    cap: int
    if limit is None:
        cap = int(os.environ.get("DAILY_DATES_LIST_LIMIT", "365"))
    elif limit == 0:
        cap = 0
    else:
        cap = limit
    dates: set[str] = set(_list_report_dates_from_disk())
    _ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """SELECT DISTINCT report_date FROM daily_reports"""
        )
        dates.update(r[0] for r in cur.fetchall())
    merged = sorted(dates, reverse=True)
    if cap > 0:
        return merged[:cap]
    return merged


def _load_report_from_json(report_date: str, exchange_id: str) -> Optional[dict]:
    path = os.path.join(
        BASE_DIR, "data", "reports", report_date, f"{exchange_id}.json"
    )
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    rows = payload.get("rows") or []
    return {
        "report_date": payload.get("report_date") or report_date,
        "exchange": payload.get("exchange") or exchange_id,
        "generated_at": payload.get("generated_at") or "",
        "timeframe": payload.get("timeframe") or _infer_report_timeframe(rows),
        "symbol_count": payload.get("symbol_count") or len(rows),
        "with_hits_count": payload.get("with_hits_count")
        or sum(1 for r in rows if r.get("hit_count", 0) > 0),
        "rows": rows,
        "gate_history_unavailable": bool(payload.get("gate_history_unavailable")),
        "gate_history_message": payload.get("gate_history_message"),
        "gate_earliest_date": payload.get("gate_earliest_date"),
    }


def get_report_by_date(report_date: str, exchange_id: Optional[str] = None) -> Optional[dict]:
    exid = exchange_id or EXCHANGE_ID
    _ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT * FROM daily_reports WHERE report_date = ? AND exchange = ?""",
            (report_date, exid),
        ).fetchone()
        if row:
            rid = row["id"]
            out_rows = _report_rows_from_db_row(conn, rid)
            return {
                "report_date": row["report_date"],
                "exchange": row["exchange"],
                "generated_at": row["generated_at"],
                "timeframe": _infer_report_timeframe(out_rows),
                "symbol_count": row["symbol_count"],
                "with_hits_count": row["with_hits_count"],
                "rows": out_rows,
            }
    return _load_report_from_json(report_date, exid)


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
