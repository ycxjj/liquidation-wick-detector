#!/usr/bin/env python3
"""
从 Binance 公开数据源下载历史K线（免费/无需API Key）
用法: python3 scripts/download_binance_public.py BTCUSDT 2023-08-17 5m
"""

import pandas as pd
import requests
import zipfile
import io
import sys
from datetime import datetime, timedelta

def download(symbol_raw='BTCUSDT', date_str='2023-08-17', timeframe='5m'):
    year_month = date_str[:7]
    url = (
        f"https://data.binance.vision/data/spot/monthly/klines/"
        f"{symbol_raw}/{timeframe}/{symbol_raw}-{timeframe}-{year_month}.zip"
    )
    
    print(f"📡 下载: {url}")
    
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
        
        filename = f"../data/{symbol_raw}_{date_str}_{timeframe}.csv"
        df_day.to_csv(filename, index=False)
        print(f"✅ 已保存: {filename} ({len(df_day)} 根K线)")
        return df_day
        
    except Exception as e:
        print(f"❌ 下载失败: {e}")
        return None

if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    date = sys.argv[2] if len(sys.argv) > 2 else '2023-08-17'
    tf = sys.argv[3] if len(sys.argv) > 3 else '5m'
    download(symbol, date, tf)
