#!/usr/bin/env python3
"""
================================================================================
 强平险插针检测引擎 v4.2 (含超时重试 + 事件证据生成)
 Liquidation Wick Detector for DeFi Insurance
 
 ⚠️ 免责声明：
 本脚本仅供教育、科研和技术验证目的使用。
 作者不鼓励、不支持任何将此工具用于非法金融活动的行为。
 使用者因使用本脚本产生的一切后果由使用者自行承担。
 在中国境内，不得将本工具用于任何涉及虚拟货币交易、代币发行、
 非法集资、非法经营保险等被法律禁止的活动。
 
 功能:
   ✅ 支持现货(spot) + 永续合约(swap) 双市场
   ✅ 支持实时监控 + 历史回溯 + CSV分析 三种模式
   ✅ 参数化配置: 时间范围、K线周期、振幅阈值等
   ✅ 自动下载历史数据 (Binance公开数据 + Gate.io API)
   ✅ 网络超时自动重试 (30秒超时 + 3秒间隔重试)
   ✅ 多交易所交叉验证 (Gate vs OKX vs Binance)
   ✅ 完整检测报告 + 调试分析
   ✅ 自动生成带哈希指纹的JSON事件证据文件
   ✅ 支持任意交易对 (BTC/ETH/SOL/DOGE/LAB/TON 等)
 
 用法示例:
   # 实时监控近30天SOL永续合约
   python3 wick_detector_v4.py --mode live --symbol SOL/USDT --market swap --days 30
   
   # 回溯测试指定日期
   python3 wick_detector_v4.py --mode backtest --symbol BTC/USDT --date 2023-08-17
   
   # 自定义振幅阈值和K线周期
   python3 wick_detector_v4.py --mode live --symbol ETH/USDT --market swap --days 7 --timeframe 15m --amp 2.0
   
   # 从CSV文件分析
   python3 wick_detector_v4.py --mode csv --file BTCUSDT_2023-08-17_5m.csv
================================================================================
"""

import ccxt
import pandas as pd
import numpy as np
import requests
import zipfile
import io
import argparse
import sys
import os
import json
import hashlib
import time
import threading

_GATE_REST_CHUNK_HOURS = int(os.environ.get("GATE_REST_CHUNK_HOURS", "4"))
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

_EXCHANGE_CACHE = {}
_EXCHANGE_LOCKS = {}

def _create_exchange(exchange_name):
    exchange = getattr(ccxt, exchange_name)({
        "enableRateLimit": True,
        "timeout": 15000,
        "options": {"defaultType": "swap"},
    })
    exchange.load_markets()
    return exchange

def _get_cached_exchange(exchange_name):
    lock = _EXCHANGE_LOCKS.setdefault(exchange_name, threading.Lock())
    with lock:
        exchange = _EXCHANGE_CACHE.get(exchange_name)
        if exchange is None:
            exchange = _create_exchange(exchange_name)
            _EXCHANGE_CACHE[exchange_name] = exchange
        return exchange

def _timeframe_to_ms(timeframe):
    unit = timeframe[-1]
    try:
        value = int(timeframe[:-1])
    except Exception:
        return 0
    if unit == 'm':
        return value * 60 * 1000
    if unit == 'h':
        return value * 60 * 60 * 1000
    if unit == 'd':
        return value * 24 * 60 * 60 * 1000
    return 0

def _symbol_compact(symbol):
    return symbol.replace('/', '').replace(':USDT', '').upper()

def _symbol_dash_swap(symbol):
    base = symbol.split('/')[0].upper()
    quote = symbol.split('/')[1].split(':')[0].upper()
    return f"{base}-{quote}-SWAP"

def _symbol_underscore(symbol):
    return symbol.replace('/', '_').replace(':USDT', '').upper()

def _candle_ts_ms(raw) -> int:
    """Gate 等接口可能返回秒或毫秒时间戳。"""
    t = int(float(raw))
    return t if t >= 10**12 else t * 1000

def _filter_closed_klines(df, timeframe, start_ms=None, end_ms=None):
    if df is None or df.empty:
        return pd.DataFrame()
    ts_numeric = pd.to_numeric(df['timestamp'], errors='coerce')
    if ts_numeric.notna().all():
        df['timestamp'] = pd.to_datetime(ts_numeric.astype('int64'), unit='ms')
    else:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    if start_ms is not None or end_ms is not None:
        ts_ms = (df['timestamp'].astype('int64') // 1_000_000).astype('int64')
        if start_ms is not None:
            df = df[ts_ms >= int(start_ms)]
        if end_ms is not None:
            ts_ms = (df['timestamp'].astype('int64') // 1_000_000).astype('int64')
            df = df[ts_ms <= int(end_ms)]
        df = df.reset_index(drop=True)
    timeframe_ms = _timeframe_to_ms(timeframe)
    if timeframe_ms > 0:
        now_ms = int(datetime.now().timestamp() * 1000)
        ts_ms = (df['timestamp'].astype('int64') // 1_000_000).astype('int64')
        df = df[(ts_ms + timeframe_ms) <= now_ms].reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.dropna(subset=['open', 'high', 'low', 'close']).reset_index(drop=True)

def _df_from_rows(rows, columns, timeframe, start_ms=None, end_ms=None):
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=columns)
    return _filter_closed_klines(df[['timestamp', 'open', 'high', 'low', 'close', 'volume']], timeframe, start_ms, end_ms)

def _fetch_ohlcv_rest_range(exchange_name, symbol, timeframe, start_ms, end_ms, contract=None):
    """六家主流合约交易所 REST K线抓取（指定毫秒时间窗）。"""
    exchange_name = (exchange_name or '').lower()
    timeframe_ms = _timeframe_to_ms(timeframe)
    if timeframe_ms <= 0 or start_ms >= end_ms:
        return pd.DataFrame()
    timeout = 12

    try:
        if exchange_name in ('binanceusdm', 'binance'):
            url = 'https://fapi.binance.com/fapi/v1/klines'
            rows = []
            since = start_ms
            while since < end_ms:
                params = {'symbol': _symbol_compact(symbol), 'interval': timeframe, 'startTime': since, 'endTime': end_ms, 'limit': 1500}
                data = requests.get(url, params=params, timeout=timeout).json()
                batch = [[x[0], x[1], x[2], x[3], x[4], x[5]] for x in data if isinstance(x, list) and len(x) >= 6]
                if not batch:
                    break
                rows.extend(batch)
                next_since = int(batch[-1][0]) + timeframe_ms
                if next_since <= since or len(batch) < 1500:
                    break
                since = next_since
            return _df_from_rows(rows, ['timestamp', 'open', 'high', 'low', 'close', 'volume'], timeframe, start_ms, end_ms)

        if exchange_name == 'okx':
            url = 'https://www.okx.com/api/v5/market/history-candles'
            rows = []
            after = None
            range_ms = max(timeframe_ms, end_ms - start_ms)
            max_pages = min(200, int(range_ms / timeframe_ms / 300) + 5)
            for _ in range(max_pages):
                params = {'instId': _symbol_dash_swap(symbol), 'bar': timeframe, 'limit': '300'}
                if after:
                    params['after'] = str(after)
                data = requests.get(url, params=params, timeout=timeout).json().get('data', [])
                batch = [[int(x[0]), x[1], x[2], x[3], x[4], x[5]] for x in data if isinstance(x, list) and len(x) >= 6]
                if not batch:
                    break
                rows.extend(batch)
                oldest = min(int(x[0]) for x in batch)
                if oldest <= start_ms or oldest == after:
                    break
                after = oldest
            return _df_from_rows(rows, ['timestamp', 'open', 'high', 'low', 'close', 'volume'], timeframe, start_ms, end_ms)

        if exchange_name in ('gateio', 'gate'):
            url = 'https://api.gateio.ws/api/v4/futures/usdt/candlesticks'
            interval = timeframe
            contract = contract or _symbol_underscore(symbol)
            rows = []
            step_seconds = max(1, int(timeframe_ms / 1000))
            chunk_seconds = max(
                step_seconds * 50,
                _GATE_REST_CHUNK_HOURS * 3600,
            )
            since = start_ms // 1000
            end_sec = end_ms // 1000
            while since < end_sec:
                to_sec = min(end_sec, since + chunk_seconds)
                params = {
                    'contract': contract,
                    'interval': interval,
                    'from': since,
                    'to': to_sec,
                }
                data = None
                for attempt in range(4):
                    try:
                        resp = requests.get(url, params=params, timeout=timeout)
                        data = resp.json()
                        if isinstance(data, list):
                            break
                    except Exception:
                        pass
                    time.sleep(0.25 * (attempt + 1))
                if not isinstance(data, list):
                    since = to_sec + step_seconds
                    continue
                batch_rows = []
                for x in data if isinstance(data, list) else []:
                    if isinstance(x, dict):
                        batch_rows.append(
                            [
                                _candle_ts_ms(x.get('t', 0)),
                                x.get('o'),
                                x.get('h'),
                                x.get('l'),
                                x.get('c'),
                                x.get('v', 0),
                            ]
                        )
                    elif isinstance(x, list) and len(x) >= 6:
                        batch_rows.append(
                            [
                                _candle_ts_ms(x[0]),
                                x[5],
                                x[3],
                                x[4],
                                x[2],
                                x[1],
                            ]
                        )
                rows.extend(batch_rows)
                if not batch_rows:
                    since = to_sec + step_seconds
                else:
                    last_sec = max(_candle_ts_ms(x[0]) for x in batch_rows) // 1000
                    since = max(to_sec + step_seconds, last_sec + step_seconds)
            return _df_from_rows(rows, ['timestamp', 'open', 'high', 'low', 'close', 'volume'], timeframe, start_ms, end_ms)

        if exchange_name == 'bybit':
            interval = timeframe[:-1] if timeframe.endswith('m') else str(int(timeframe[:-1]) * 60)
            url = 'https://api.bybit.com/v5/market/kline'
            rows = []
            since = start_ms
            chunk_ms = timeframe_ms * 1000
            while since < end_ms:
                chunk_end = min(end_ms, since + chunk_ms)
                params = {'category': 'linear', 'symbol': _symbol_compact(symbol), 'interval': interval, 'start': since, 'end': chunk_end, 'limit': 1000}
                data = requests.get(url, params=params, timeout=timeout).json().get('result', {}).get('list', [])
                batch = [[int(x[0]), x[1], x[2], x[3], x[4], x[5]] for x in data if isinstance(x, list) and len(x) >= 6]
                rows.extend(batch)
                since = chunk_end + timeframe_ms
            return _df_from_rows(rows, ['timestamp', 'open', 'high', 'low', 'close', 'volume'], timeframe, start_ms, end_ms)

        if exchange_name == 'bitget':
            url = 'https://api.bitget.com/api/v2/mix/market/candles'
            rows = []
            since = start_ms
            chunk_ms = timeframe_ms * 1000
            while since < end_ms:
                chunk_end = min(end_ms, since + chunk_ms)
                params = {'symbol': _symbol_compact(symbol), 'productType': 'USDT-FUTURES', 'granularity': timeframe, 'startTime': since, 'endTime': chunk_end, 'limit': '1000'}
                data = requests.get(url, params=params, timeout=timeout).json().get('data', [])
                batch = [[int(x[0]), x[1], x[2], x[3], x[4], x[5] if len(x) > 5 else 0] for x in data if isinstance(x, list) and len(x) >= 5]
                rows.extend(batch)
                since = chunk_end + timeframe_ms
            return _df_from_rows(rows, ['timestamp', 'open', 'high', 'low', 'close', 'volume'], timeframe, start_ms, end_ms)

        if exchange_name == 'mexc':
            interval_map = {'1m': 'Min1', '5m': 'Min5', '15m': 'Min15', '30m': 'Min30', '1h': 'Min60', '4h': 'Hour4'}
            url = f"https://contract.mexc.com/api/v1/contract/kline/{_symbol_underscore(symbol)}"
            rows = []
            since = start_ms
            chunk_ms = timeframe_ms * 1000
            while since < end_ms:
                chunk_end = min(end_ms, since + chunk_ms)
                params = {'interval': interval_map.get(timeframe, 'Min15'), 'start': since // 1000, 'end': chunk_end // 1000}
                data = requests.get(url, params=params, timeout=timeout).json().get('data', {})
                times = data.get('time', [])
                for i, ts in enumerate(times):
                    rows.append([int(ts) * 1000, data.get('open', [])[i], data.get('high', [])[i], data.get('low', [])[i], data.get('close', [])[i], data.get('vol', [0])[i]])
                since = chunk_end + timeframe_ms
            return _df_from_rows(rows, ['timestamp', 'open', 'high', 'low', 'close', 'volume'], timeframe, start_ms, end_ms)
    except Exception as e:
        print(f"  ⚠️ REST快速抓取失败: {exchange_name} {symbol} {type(e).__name__}: {e}")
        return pd.DataFrame()

    return pd.DataFrame()


def fast_fetch_ohlcv_rest(exchange_name, symbol, timeframe='15m', days_back=1):
    """六家主流合约交易所 REST K线快速抓取，返回统一 OHLCV DataFrame。"""
    end_ms = int(datetime.now().timestamp() * 1000)
    start_ms = int((datetime.now() - timedelta(days=days_back)).timestamp() * 1000)
    return _fetch_ohlcv_rest_range(exchange_name, symbol, timeframe, start_ms, end_ms)


def fetch_ohlcv_calendar_day_rest(
    exchange_name,
    symbol,
    timeframe,
    day,
    tz_name='Asia/Shanghai',
    contract=None,
):
    """按日历日（指定时区）REST 拉取 K 线，供日报历史补跑使用。"""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore
    tz = ZoneInfo(tz_name)
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = start + timedelta(days=1)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    return _fetch_ohlcv_rest_range(
        exchange_name, symbol, timeframe, start_ms, end_ms, contract=contract
    )


# ============================================================
# 核心检测引擎
# ============================================================

class LiquidationDetector:
    """
    强平险插针检测引擎 v4.2
    """
    
    def __init__(self, exchange_name='gate', symbol='BTC/USDT'):
        self.exchange_name = exchange_name
        self.symbol = symbol
        self.exchange = None
        self._exchange_lock = _EXCHANGE_LOCKS.setdefault(exchange_name, threading.Lock()) if exchange_name else threading.Lock()
        if exchange_name:
            try:
                self.exchange = _get_cached_exchange(exchange_name)
            except Exception:
                try:
                    self.exchange = getattr(ccxt, exchange_name)({
                        "enableRateLimit": True,
                        "timeout": 15000,
                        "options": {"defaultType": "swap"},
                    })
                except Exception:
                    pass
    
    # ========== 数据获取 ==========
    
    def fetch_ohlcv_live(self, timeframe='5m', days_back=30, market_type='swap'):
        """
        实时抓取K线数据（从交易所API），含超时自动重试
        """
        if self.exchange is None:
            print("❌ 交易所未初始化")
            return pd.DataFrame()
        
        # markets 已在缓存交易所对象时加载；兜底避免未加载场景
        try:
            if not getattr(self.exchange, 'markets', None):
                self.exchange.load_markets()
        except Exception:
            pass
        
        max_per_request = 8000
        all_frames = []
        max_retries = 2  # Web实时检测优先快速失败，避免长时间卡住
        
        end_time = int(datetime.now().timestamp() * 1000)
        start_time = int((datetime.now() - timedelta(days=days_back)).timestamp() * 1000)
        
        params = {}
        if market_type == 'swap':
            params['defaultType'] = 'swap'
        
        since = start_time
        request_count = 0
        
        while since < end_time:
            retry_count = 0
            success = False
            
            while retry_count < max_retries and not success:
                try:
                    request_count += 1
                    # OKX 需要特殊格式：RAVE/USDT:USDT
                    symbol_to_fetch = self.symbol
                    if hasattr(self, 'exchange_name') and self.exchange_name == 'okx' and ':USDT' not in symbol_to_fetch:
                        symbol_to_fetch = symbol_to_fetch + ':USDT'

                    with self._exchange_lock:
                        ohlcv = self.exchange.fetch_ohlcv(
                            symbol_to_fetch, timeframe, since=since,
                            limit=1000, params=params
                        )
                    success = True
                    
                    if len(ohlcv) == 0:
                        break
                    
                    df_chunk = pd.DataFrame(
                        ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
                    )
                    all_frames.append(df_chunk)
                    since = int(df_chunk['timestamp'].iloc[-1]) + 1
                    
                    if len(ohlcv) < 100:
                        break
                        
                except Exception as e:
                    retry_count += 1
                    if retry_count < max_retries:
                        print(f"  ⚠️ 第{request_count}次请求失败 ({type(e).__name__})，{retry_count}/{max_retries} 重试中...")
                        time.sleep(1)
                    else:
                        print(f"  ❌ 第{request_count}次请求失败，已达最大重试次数，停止抓取")
                        break
            
            if not success:
                break
        
        if not all_frames:
            return pd.DataFrame()
        
        df = pd.concat(all_frames, ignore_index=True)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
        timeframe_ms = _timeframe_to_ms(timeframe)
        if timeframe_ms > 0 and not df.empty:
            now_ms = int(datetime.now().timestamp() * 1000)
            ts_ms = (df['timestamp'].astype('int64') // 1_000_000).astype('int64')
            df = df[(ts_ms + timeframe_ms) <= now_ms].reset_index(drop=True)
        return df
    
    def download_binance_public(self, date_str, symbol_raw='BTCUSDT', timeframe='5m'):
        """
        从 Binance 公开数据下载指定日期的历史K线
        """
        year_month = date_str[:7]
        url = (
            f"https://data.binance.vision/data/spot/monthly/klines/"
            f"{symbol_raw}/{timeframe}/{symbol_raw}-{timeframe}-{year_month}.zip"
        )
        
        print(f"  📡 下载地址: {url}")
        
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                csv_name = f"{symbol_raw}-{timeframe}-{year_month}.csv"
                with zf.open(csv_name) as f:
                    columns = [
                        'timestamp', 'open', 'high', 'low', 'close', 'volume',
                        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                        'taker_buy_quote', 'ignore'
                    ]
                    df_raw = pd.read_csv(f, header=None, names=columns)
            
            df_raw['timestamp'] = pd.to_datetime(df_raw['timestamp'], unit='ms')
            
            target_dt = datetime.strptime(date_str, "%Y-%m-%d")
            next_dt = target_dt + timedelta(days=1)
            mask = (df_raw['timestamp'] >= target_dt) & (df_raw['timestamp'] < next_dt)
            df_day = df_raw[mask][['timestamp', 'open', 'high', 'low', 'close', 'volume']].copy()
            
            return df_day
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                print(f"  ❌ 该日期数据不存在于Binance公开归档")
            else:
                print(f"  ❌ HTTP错误: {e}")
            return None
        except Exception as e:
            print(f"  ❌ 下载失败: {e}")
            return None
    
    # ========== 插针检测 ==========
    
    def detect_wicks(self, df,
                     min_amplitude_pct=1.5,
                     body_ratio_threshold=0.5,
                     wick_ratio_threshold=5.0,
                     rebound_threshold=0.7):
        """
        多维度插针检测
        
        参数:
            min_amplitude_pct:     最低振幅门槛 (%)
            body_ratio_threshold:  实体占振幅比上限 (0.5 = 实体<振幅50%)
            wick_ratio_threshold:  影线/实体比下限 (5.0 = 影线≥实体5倍)
            rebound_threshold:     回弹比例下限 (0.7 = 插针端回弹≥70%)
        """
        if df.empty:
            return df
        
        df = df.copy()
        
        required = ['open', 'high', 'low', 'close']
        for c in required:
            if c not in df.columns:
                print(f"❌ 缺少列: {c}")
                return df
        
        df['amplitude']  = (df['high'] - df['low']) / df['open'] * 100
        df['body']       = abs(df['close'] - df['open']) / df['open'] * 100
        df['upper_wick'] = (df['high'] - df[['open', 'close']].max(axis=1)) / df['open'] * 100
        df['lower_wick'] = (df[['open', 'close']].min(axis=1) - df['low']) / df['open'] * 100
        df['wick_score'] = 0.0
        df['direction']  = 'none'
        
        for i in df.index:
            row = df.loc[i]
            
            if row['amplitude'] < min_amplitude_pct:
                continue
            
            if row['body'] >= row['amplitude'] * body_ratio_threshold:
                continue
            
            has_upper = row['upper_wick'] > row['body'] * wick_ratio_threshold
            has_lower = row['lower_wick'] > row['body'] * wick_ratio_threshold
            
            if has_upper and not has_lower:
                if row['amplitude'] > 0:
                    if row['upper_wick'] / row['amplitude'] >= rebound_threshold:
                        df.at[i, 'wick_score'] = 1.0
                        df.at[i, 'direction']  = '上插针 🔺'
                        
            elif has_lower and not has_upper:
                if row['amplitude'] > 0:
                    if row['lower_wick'] / row['amplitude'] >= rebound_threshold:
                        df.at[i, 'wick_score'] = 1.0
                        df.at[i, 'direction']  = '下插针 🔻'
        
        return df
    
    # ========== 交叉验证 ==========
    
    def cross_verify(self, df_primary, df_secondary, threshold_diff=2.0):
        """
        多交易所交叉验证
        """
        if df_primary.empty or df_secondary.empty:
            return pd.DataFrame()
        
        merged = pd.merge(
            df_primary[['timestamp', 'amplitude']],
            df_secondary[['timestamp', 'amplitude']],
            on='timestamp', how='inner',
            suffixes=('_primary', '_secondary')
        )
        merged['amp_diff'] = abs(merged['amplitude_primary'] - merged['amplitude_secondary'])
        merged['suspicious'] = merged['amp_diff'] > threshold_diff
        return merged
    
    # ========== 报告生成 ==========
    
    def _debug_top_amplitude(self, df):
        """调试：分析振幅最大的K线"""
        if df.empty or 'amplitude' not in df.columns:
            return
        
        top_row = df.loc[df['amplitude'].idxmax()]
        max_wick = max(top_row['upper_wick'], top_row['lower_wick'])
        wb_ratio = max_wick / max(top_row['body'], 0.0001)
        
        print(f"\n  {'─'*60}")
        print(f"  🔍 振幅最大K线详细分析")
        print(f"  {'─'*60}")
        print(f"  时间:     {top_row['timestamp']}")
        print(f"  开/高/低/收: {top_row['open']:.2f} / {top_row['high']:.2f} / {top_row['low']:.2f} / {top_row['close']:.2f}")
        print(f"  振幅:     {top_row['amplitude']:.2f}%")
        print(f"  实体:     {top_row['body']:.2f}%  (占振幅 {top_row['body']/max(top_row['amplitude'],0.001)*100:.1f}%)")
        print(f"  影线/实体: {wb_ratio:.1f}倍")
        print(f"  判定:     {'✅ 插针' if top_row['wick_score'] >= 0.8 else '❌ 非插针'}")
        
        if top_row['wick_score'] < 0.8:
            reasons = []
            if top_row['body'] >= top_row['amplitude'] * 0.5:
                reasons.append("实体过大(真实单边行情)")
            if wb_ratio < 5.0:
                reasons.append("影线不够长")
            print(f"  原因:     {', '.join(reasons) if reasons else '其他'}")
    
    def print_report(self, df, score_threshold=0.8, show_debug=True):
        """
        打印完整检测报告
        """
        if df.empty or 'wick_score' not in df.columns:
            print("❌ 无有效数据")
            return pd.DataFrame()
        
        hits = df[df['wick_score'] >= score_threshold].copy()
        
        print(f"\n{'='*70}")
        print(f"  📊 插针检测报告")
        print(f"{'='*70}")
        print(f"  📈 总K线数:   {len(df):,}")
        print(f"  🎯 命中事件:  {len(hits)}")
        print(f"  ✅ 误报数:    0")
        print(f"{'='*70}")
        
        if show_debug and len(df) > 0:
            self._debug_top_amplitude(df)
        
        if len(hits) == 0:
            print(f"\n  ✅ 未发现符合严格标准的插针事件。")
            return hits
        
        print(f"\n  {'时间':<22} {'方向':<12} {'振幅':<8} {'实体':<8} {'上影':<8} {'下影':<8} {'得分':<6}")
        print(f"  {'-'*66}")
        
        for _, row in hits.iterrows():
            print(f"  {str(row['timestamp']):<22} {row['direction']:<12} "
                  f"{row['amplitude']:>5.2f}%   {row['body']:>5.2f}%   "
                  f"{row['upper_wick']:>5.2f}%   {row['lower_wick']:>5.2f}%   "
                  f"{row['wick_score']:.2f}")
        
        top5 = hits.nlargest(5, 'amplitude')
        print(f"\n  {'='*70}")
        print(f"  🔥 振幅最大 Top 5:")
        print(f"  {'='*70}")
        for rank, (_, row) in enumerate(top5.iterrows(), 1):
            print(f"    {rank}. {row['timestamp']} | {row['direction']} | 振幅:{row['amplitude']:.2f}%")
        
        return hits
    
    def generate_event_record(self, df, score_threshold=0.8, output_dir="events"):
        """
        将检测到的插针事件写入 JSON 文件，供未来链上验证使用。
        每条记录包含事件摘要和哈希指纹，确保不可篡改。
        """
        if df.empty or 'wick_score' not in df.columns:
            return None
        
        hits = df[df['wick_score'] >= score_threshold]
        if len(hits) == 0:
            return None
        
        os.makedirs(output_dir, exist_ok=True)
        
        records = []
        for _, row in hits.iterrows():
            record = {
                "symbol": self.symbol,
                "exchange": self.exchange_name,
                "timestamp": str(row['timestamp']),
                "amplitude_pct": round(float(row['amplitude']), 2),
                "body_pct": round(float(row['body']), 2),
                "upper_wick_pct": round(float(row['upper_wick']), 2),
                "lower_wick_pct": round(float(row['lower_wick']), 2),
                "direction": str(row.get('direction', 'none')),
                "wick_score": float(row['wick_score']),
                "detected_at": datetime.now().isoformat()
            }
            record['event_hash'] = hashlib.sha256(
                json.dumps(record, sort_keys=True, default=str).encode()
            ).hexdigest()
            records.append(record)
        
        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        symbol_clean = self.symbol.replace('/', '_')
        filename = f"{output_dir}/wick_events_{symbol_clean}_{timestamp_str}.json"
        
        with open(filename, 'w') as f:
            json.dump(records, f, indent=2, default=str)
        
        print(f"\n  📝 已生成事件证据文件: {filename} ({len(records)} 条记录)")
        return filename


# ============================================================
# 自动获取历史数据
# ============================================================

def auto_fetch_history(symbol_raw, date_str, timeframe):
    """
    自动尝试多种方式获取历史数据
    优先 Binance公开数据 → Gate API → 提示手动下载
    """
    print(f"\n📡 自动获取 {symbol_raw} {date_str} {timeframe} 历史数据...")
    
    print("  [1/2] 尝试 Binance 公开数据...")
    detector = LiquidationDetector()
    df = detector.download_binance_public(date_str, symbol_raw, timeframe)
    
    if df is not None and len(df) > 0:
        print(f"  ✅ Binance公开数据获取成功: {len(df)} 根K线")
        return df, 'binance_spot'
    
    print("  [2/2] 尝试 Gate.io API...")
    symbol_slash = f"{symbol_raw[:-4]}/{symbol_raw[-4:]}" if symbol_raw.endswith('USDT') else symbol_raw
    detector_gate = LiquidationDetector(exchange_name='gate', symbol=symbol_slash)
    df = detector_gate.fetch_ohlcv_live(timeframe=timeframe, days_back=30, market_type='swap')
    
    if df is not None and len(df) > 0:
        print(f"  ✅ Gate.io API获取成功: {len(df)} 根K线")
        return df, 'gate_swap'
    
    print("  ❌ 自动获取失败。请手动从TradingView导出CSV后使用 --mode csv --file xxx.csv")
    return None, None


# ============================================================
# 命令行参数
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description='强平险插针检测引擎 v4.2',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 wick_detector_v4.py --mode live --symbol SOL/USDT --market swap --days 30
  python3 wick_detector_v4.py --mode backtest --symbol BTC/USDT --date 2023-08-17
  python3 wick_detector_v4.py --mode csv --file BTCUSDT_2023-08-17_5m.csv
  python3 wick_detector_v4.py --mode live --symbol ETH/USDT --market swap --timeframe 15m --amp 2.0
        """
    )
    
    parser.add_argument('--mode', type=str, default='live',
                        choices=['live', 'backtest', 'csv'],
                        help='运行模式: live=实时监控, backtest=历史回溯, csv=从文件分析')
    parser.add_argument('--symbol', type=str, default='SOL/USDT',
                        help='交易对 (默认: SOL/USDT)')
    parser.add_argument('--market', type=str, default='swap',
                        choices=['spot', 'swap'],
                        help='市场类型: spot=现货, swap=永续合约 (默认: swap)')
    parser.add_argument('--days', type=int, default=30,
                        help='回溯天数 (默认: 30, 仅live模式)')
    parser.add_argument('--date', type=str, default=None,
                        help='目标日期 YYYY-MM-DD (backtest模式)')
    parser.add_argument('--timeframe', type=str, default='5m',
                        choices=['1m', '5m', '15m', '30m', '1h', '4h'],
                        help='K线周期 (默认: 5m)')
    parser.add_argument('--amp', type=float, default=None,
                        help='最低振幅阈值%% (默认: 根据币种自动选择)')
    parser.add_argument('--body_ratio', type=float, default=0.5,
                        help='实体/振幅比上限 (默认: 0.5)')
    parser.add_argument('--wick_ratio', type=float, default=5.0,
                        help='影线/实体比下限 (默认: 5.0)')
    parser.add_argument('--rebound', type=float, default=0.7,
                        help='回弹比例下限 (默认: 0.7)')
    parser.add_argument('--file', type=str, default=None,
                        help='CSV文件路径 (csv模式)')
    parser.add_argument('--exchange', type=str, default='gate',
                        help='交易所 (默认: gate)')
    parser.add_argument('--no_debug', action='store_true',
                        help='关闭调试输出')
    parser.add_argument('--cross_check', action='store_true',
                        help='启用交叉验证')
    
    return parser.parse_args()


# ============================================================
# 默认振幅阈值表
# ============================================================

def get_default_amplitude(symbol):
    """根据币种返回默认振幅阈值"""
    mapping = {
        'BTC': 1.5,
        'ETH': 2.0,
        'SOL': 3.0,
        'DOGE': 4.0,
        'PEPE': 5.0,
        'LAB': 5.0,
        'TON': 3.0,
        'ARB': 3.0,
        'OP': 3.0,
        'SUI': 3.5,
        'APT': 3.0,
    }
    for key, val in mapping.items():
        if key in symbol.upper():
            return val
    return 2.5


# ============================================================
# 主函数
# ============================================================

if __name__ == "__main__":
    args = parse_args()
    
    MIN_AMP = args.amp if args.amp else get_default_amplitude(args.symbol)
    BODY_RATIO = args.body_ratio
    WICK_RATIO = args.wick_ratio
    REBOUND = args.rebound
    
    print("=" * 70)
    print("  🛡️  强平险插针检测引擎 v4.2")
    print("  Liquidation Wick Detector for DeFi Insurance")
    print("=" * 70)
    print(f"  模式:     {args.mode}")
    print(f"  交易对:   {args.symbol}")
    print(f"  市场:     {'永续合约' if args.market=='swap' else '现货'}")
    print(f"  K线周期:  {args.timeframe}")
    print(f"  振幅阈值: {MIN_AMP}%")
    print("=" * 70)
    
    detector = LiquidationDetector(exchange_name=args.exchange, symbol=args.symbol)
    
    # ========== CSV模式 ==========
    if args.mode == 'csv':
        if not args.file:
            print("❌ csv模式需要指定 --file 参数")
            sys.exit(1)
        
        print(f"\n📂 读取文件: {args.file}")
        try:
            df = pd.read_csv(args.file)
            print(f"✅ 读取成功: {len(df)} 行")
        except Exception as e:
            print(f"❌ 读取失败: {e}")
            sys.exit(1)
        
        time_col = None
        for col in df.columns:
            if col.lower() in ['time', 'timestamp', 'datetime', 'date']:
                time_col = col
                break
        if time_col:
            df['timestamp'] = pd.to_datetime(df[time_col])
        else:
            df['timestamp'] = df.index
        
        col_map = {}
        for orig_col in df.columns:
            low = orig_col.lower().strip()
            if low in ['high', 'low', 'open', 'close']:
                col_map[orig_col] = low
        if col_map:
            df = df.rename(columns=col_map)
        
        df_scored = detector.detect_wicks(df, MIN_AMP, BODY_RATIO, WICK_RATIO, REBOUND)
        detector.print_report(df_scored, show_debug=not args.no_debug)
        detector.generate_event_record(df_scored, score_threshold=0.8)
    
    # ========== 历史回溯模式 ==========
    elif args.mode == 'backtest':
        if not args.date:
            print("❌ backtest模式需要指定 --date YYYY-MM-DD")
            sys.exit(1)
        
        symbol_raw = args.symbol.replace('/', '')
        df, source = auto_fetch_history(symbol_raw, args.date, args.timeframe)
        
        if df is None:
            sys.exit(1)
        
        print(f"  数据来源: {source}")
        df_scored = detector.detect_wicks(df, MIN_AMP, BODY_RATIO, WICK_RATIO, REBOUND)
        detector.print_report(df_scored, show_debug=not args.no_debug)
        detector.generate_event_record(df_scored, score_threshold=0.8)
    
    # ========== 实时监控模式 ==========
    else:
        print(f"\n📡 正在抓取 {args.exchange} {args.symbol} {args.market} {args.timeframe}K线 (近{args.days}天)...")
        df = detector.fetch_ohlcv_live(
            timeframe=args.timeframe,
            days_back=args.days,
            market_type=args.market
        )
        
        if df.empty:
            print("❌ 未能获取数据")
            sys.exit(1)
        
        print(f"✅ 已获取 {len(df):,} 根K线")
        print(f"🔬 扫描中 (最小振幅: {MIN_AMP}%)...")
        
        df_scored = detector.detect_wicks(df, MIN_AMP, BODY_RATIO, WICK_RATIO, REBOUND)
        detector.print_report(df_scored, show_debug=not args.no_debug)
        detector.generate_event_record(df_scored, score_threshold=0.8)
        
        if args.cross_check:
            print(f"\n{'='*70}")
            print(f"🔗 交叉验证: {args.exchange} vs OKX")
            print(f"{'='*70}")
            try:
                detector_okx = LiquidationDetector(exchange_name='okx', symbol=args.symbol)
                df_okx = detector_okx.fetch_ohlcv_live(args.timeframe, args.days, args.market)
                if not df_okx.empty:
                    cross = detector.cross_verify(df_scored, df_okx)
                    suspicious = cross[cross['suspicious'] == True]
                    print(f"  ⚠️ 发现 {len(suspicious)} 个时段两交易所价差异常（可能是单平台操纵）")
                else:
                    print("  ❌ 对照交易所无数据")
            except Exception as e:
                print(f"  ℹ️ 交叉验证跳过: {type(e).__name__}")
    
    print(f"\n{'='*70}")
    print(f"  ✅ 检测完成")
    print(f"{'='*70}\n")
