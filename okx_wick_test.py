#!/usr/bin/env python3
"""专用测试脚本：OKX RAVE/USDT 数据抓取（修正格式）"""
import ccxt, time, traceback
from datetime import datetime, timedelta

SYMBOL = 'RAVE/USDT:USDT'  # OKX 永续合约的正确格式
DAYS = 10

print(f"[{datetime.now()}] 开始测试 OKX {SYMBOL} 数据抓取")

try:
    exchange = ccxt.okx({'enableRateLimit': True, 'timeout': 30000})
    exchange.load_markets()
    
    end_ts = int(datetime.now().timestamp() * 1000)
    start_ts = int((datetime.now() - timedelta(days=DAYS)).timestamp() * 1000)
    
    all_data = []
    since = start_ts
    
    print(f"  时间范围: {datetime.fromtimestamp(start_ts/1000)} -> {datetime.fromtimestamp(end_ts/1000)}")
    
    while since < end_ts:
        try:
            ohlcv = exchange.fetch_ohlcv(SYMBOL, '5m', since=since, limit=500, params={'defaultType': 'swap'})
            
            if not ohlcv:
                print(f"  ✗ 服务器未返回数据")
                break
            
            for candle in ohlcv:
                ts = candle[0]
                if start_ts <= ts < end_ts:
                    all_data.append(candle)
            
            since = ohlcv[-1][0] + 1
            print(f"  ↓ 已抓取 {len(ohlcv)} 根K线，累计目标数据 {len(all_data)} 根")
            
            if len(ohlcv) < 100:
                break
            
            time.sleep(exchange.rateLimit / 1000 * 2)
            
        except Exception as e:
            print(f"  ✗ 请求出错: {type(e).__name__} - {str(e)[:200]}")
            break
    
    if all_data:
        first_ts = all_data[0][0]
        last_ts = all_data[-1][0]
        print(f"\n[结果] 成功抓取 {len(all_data)} 根目标K线")
        print(f"[结果] 时间跨度: {datetime.fromtimestamp(first_ts/1000)} -> {datetime.fromtimestamp(last_ts/1000)}")
    else:
        print(f"\n[结果] 未抓取到任何目标数据")
        
        try:
            recent = exchange.fetch_ohlcv(SYMBOL, '5m', limit=10, params={'defaultType': 'swap'})
            if recent:
                print(f"[诊断] 该交易对有近期数据，但历史数据获取失败")
                last_time = datetime.fromtimestamp(recent[-1][0]/1000)
                print(f"[诊断] 最近数据时间: {last_time}")
            else:
                print(f"[诊断] 该交易对完全没有数据")
        except Exception as de:
            print(f"[诊断] 诊断失败: {type(de).__name__} - {str(de)[:200]}")

except Exception as e:
    print(f"错误: {traceback.format_exc()}")

print(f"[{datetime.now()}] 测试结束")
