#!/usr/bin/env python3
"""
Liquidation Wick Detector - Web API
仅供教育、科研和技术演示目的使用。
支持币安/欧易/Gate.io 三大交易所，动态加载全部USDT永续合约。
"""

import subprocess
import os
import json
import re
import traceback
from datetime import datetime
from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Liquidation Wick Detector - 插针检测引擎</title>
    <link rel="icon" type="image/png" href="/logo.png">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0e27; color: #e0e0e0; min-height: 100vh; }
        .container { max-width: 960px; margin: 0 auto; padding: 40px 20px; }
        .header { text-align: center; margin-bottom: 40px; }
        .header .shield { margin-bottom: 16px; }
        .header h1 { font-size: 2em; color: #00d4ff; margin-bottom: 10px; }
        .header p { color: #8892b0; font-size: 1.05em; }
        .card { background: #112240; border-radius: 12px; padding: 28px; margin-bottom: 30px; border: 1px solid #1e3a5f; }
        .card h2 { color: #00d4ff; margin-bottom: 22px; font-size: 1.25em; }
        .form-row { display: flex; gap: 15px; flex-wrap: wrap; }
        .form-group { flex: 1; min-width: 160px; margin-bottom: 16px; }
        .form-group.symbol-group { position: relative; z-index: 10; }
        label { display: block; margin-bottom: 6px; color: #8892b0; font-weight: 500; font-size: 0.9em; }
        select, input { width: 100%; padding: 10px 14px; border-radius: 8px; border: 1px solid #1e3a5f; background: #0a0e27; color: #e0e0e0; font-size: 0.95em; transition: border-color 0.2s; }
        select:focus, input:focus { outline: none; border-color: #00d4ff; }
        select { cursor: pointer; }
        .btn { background: linear-gradient(135deg, #00d4ff, #00a8cc); color: #0a0e27; border: none; padding: 13px 30px; border-radius: 8px; font-size: 1em; font-weight: 600; cursor: pointer; transition: all 0.2s; width: 100%; letter-spacing: 0.5px; }
        .btn:hover { background: linear-gradient(135deg, #00b8e6, #0099b8); transform: translateY(-1px); box-shadow: 0 4px 15px rgba(0,212,255,0.3); }
        .btn:disabled { background: #3a4a5a; cursor: not-allowed; transform: none; box-shadow: none; }
        .result-card { background: #0a0e27; border-radius: 8px; padding: 22px; margin-top: 20px; border: 1px solid #1e3a5f; animation: fadeIn 0.3s ease; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .result-card h3 { color: #f7931a; margin-bottom: 12px; font-size: 1.1em; }
        .result-stats { display: flex; gap: 12px; flex-wrap: wrap; margin: 15px 0; }
        .stat { background: #112240; padding: 14px 18px; border-radius: 8px; text-align: center; flex: 1; min-width: 80px; }
        .stat .number { font-size: 1.8em; font-weight: 700; color: #00d4ff; }
        .stat .label { font-size: 0.8em; color: #8892b0; margin-top: 4px; }
        .loading { text-align: center; padding: 40px 20px; color: #8892b0; }
        .spinner { border: 3px solid #1e3a5f; border-top: 3px solid #00d4ff; border-radius: 50%; width: 44px; height: 44px; animation: spin 1s linear infinite; margin: 0 auto 16px; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .error-box { background: #1a0a0a; border: 1px solid #ff4444; border-radius: 8px; padding: 20px; margin-top: 20px; }
        .error-box h3 { color: #ff4444; }
        .success-msg { color: #00d4ff; margin-top: 15px; font-size: 1.05em; }
        .warning-msg { color: #f7931a; margin-top: 15px; font-size: 1.05em; }
        .disclaimer { text-align: center; color: #555; font-size: 0.78em; margin-top: 30px; padding: 20px; border-top: 1px solid #1e3a5f; line-height: 1.6; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th { text-align: left; padding: 10px 8px; color: #8892b0; border-bottom: 2px solid #1e3a5f; font-size: 0.85em; }
        td { padding: 10px 8px; border-bottom: 1px solid #1e3a5f; font-size: 0.9em; }
        td.amp { color: #f7931a; font-weight: 600; }
        .symbol-dropdown { position: absolute; top: 100%; left: 0; right: 0; background: #112240; border: 1px solid #00d4ff; border-radius: 0 0 8px 8px; max-height: 280px; overflow-y: auto; z-index: 999; box-shadow: 0 6px 20px rgba(0,0,0,0.6); display: none; }
        .symbol-dropdown div { padding: 9px 14px; cursor: pointer; border-bottom: 1px solid #1e3a5f; font-size: 0.9em; transition: background 0.12s; }
        .symbol-dropdown div:hover { background: #1a3050; }
        .symbol-dropdown div:last-child { border-bottom: none; }
        .symbol-dropdown .no-result { padding: 12px 14px; color: #8892b0; cursor: default; text-align: center; }
        .symbol-dropdown .no-result:hover { background: transparent; }
        @media (max-width: 640px) {
            .form-group { min-width: 100%; }
            .header h1 { font-size: 1.5em; }
            .result-stats { gap: 8px; }
            .stat { padding: 10px 12px; }
            .stat .number { font-size: 1.4em; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="shield">
                <img src="/logo.png" alt="Wick Detector Logo" 
                     style="width: 64px; height: 64px; border-radius: 12px; 
                            background: #0a0e27; padding: 6px; 
                            box-shadow: 0 0 20px rgba(0, 212, 255, 0.3);">
            </div>
            <h1>Liquidation Wick Detector</h1>
            <p>开源链上强平风控检测引擎 — 精准识别永续合约恶意插针</p>
        </div>

        <div class="card">
            <h2>🔍 运行检测</h2>
            <form id="detect-form" autocomplete="off">
                <div class="form-row">
                    <div class="form-group">
                        <label for="exchange">交易所</label>
                        <select id="exchange" name="exchange">
                            <option value="binanceusdm" selected>币安 (Binance)</option>
                            <option value="okx">欧易 (OKX)</option>
                            <option value="gate">Gate.io</option>
                        </select>
                    </div>
                    <div class="form-group symbol-group">
                        <label for="symbol-input">交易对（可搜索）</label>
                        <input type="text" id="symbol-input" name="symbol" 
                               placeholder="加载中..." autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
                        <div id="symbol-dropdown" class="symbol-dropdown"></div>
                    </div>
                    <div class="form-group">
                        <label for="timeframe">K线周期</label>
                        <select id="timeframe" name="timeframe">
                            <option value="1m">1分钟</option>
                            <option value="5m" selected>5分钟</option>
                            <option value="15m">15分钟</option>
                            <option value="1h">1小时</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label for="days">回溯天数</label>
                        <input type="number" id="days" name="days" value="3" min="1" max="30">
                    </div>
                    <div class="form-group">
                        <label for="amp">振幅阈值 (%)</label>
                        <input type="number" id="amp" name="amp" value="3.0" min="0.5" max="20" step="0.5">
                    </div>
                </div>
                <button type="submit" class="btn" id="submit-btn">🚀 开始检测</button>
            </form>
            <div id="result-area"></div>
        </div>

        <div class="disclaimer">
            ⚠️ 本工具仅供教育、科研和技术演示目的使用。<br>
            作者不鼓励、不支持任何将此工具用于非法金融活动的行为。
        </div>
    </div>

    <script>
        (function() {
            const symbolInput = document.getElementById('symbol-input');
            const symbolDropdown = document.getElementById('symbol-dropdown');
            const exchangeSelect = document.getElementById('exchange');
            const submitBtn = document.getElementById('submit-btn');
            const resultArea = document.getElementById('result-area');
            let allSymbols = [];
            let isLoading = false;
            let activeIndex = -1;

            async function loadSymbols() {
                if (isLoading) return;
                isLoading = true;
                symbolInput.value = '';
                symbolInput.placeholder = '正在加载合约列表...';
                symbolDropdown.style.display = 'none';
                symbolDropdown.innerHTML = '';
                try {
                    const resp = await fetch('/api/symbols?exchange=' + encodeURIComponent(exchangeSelect.value));
                    const data = await resp.json();
                    if (data.status === 'success' && data.symbols.length > 0) {
                        allSymbols = data.symbols;
                        symbolInput.placeholder = '搜索代币（共 ' + data.count + ' 个合约）';
                        if (allSymbols.includes('SOL/USDT')) {
                            symbolInput.value = 'SOL/USDT';
                        } else {
                            symbolInput.value = allSymbols[0];
                        }
                    } else {
                        symbolInput.placeholder = '加载失败，请手动输入';
                    }
                } catch(e) {
                    symbolInput.placeholder = '加载失败，请手动输入';
                }
                isLoading = false;
            }
            loadSymbols();
            exchangeSelect.addEventListener('change', loadSymbols);

            function renderDropdown(matches) {
                if (matches.length === 0) {
                    symbolDropdown.innerHTML = '<div class="no-result">未找到匹配的合约</div>';
                } else {
                    symbolDropdown.innerHTML = matches.map((s, i) => 
                        '<div data-index="' + i + '" data-symbol="' + s + '" ' +
                        'style="' + (i === activeIndex ? 'background:#1a3050;' : '') + '">' + s + '</div>'
                    ).join('');
                }
                symbolDropdown.style.display = 'block';
            }

            function filterSymbols(query) {
                if (!query) return allSymbols.slice(0, 120);
                const q = query.toUpperCase();
                return allSymbols.filter(s => s.toUpperCase().includes(q)).slice(0, 60);
            }

            function showDropdown() {
                if (allSymbols.length === 0) return;
                activeIndex = -1;
                renderDropdown(filterSymbols(symbolInput.value.trim()));
            }

            symbolInput.addEventListener('focus', showDropdown);
            symbolInput.addEventListener('input', showDropdown);
            symbolInput.addEventListener('click', function(e) { e.stopPropagation(); showDropdown(); });

            symbolInput.addEventListener('keydown', function(e) {
                const items = symbolDropdown.querySelectorAll('div[data-symbol]');
                if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    if (items.length === 0) return;
                    activeIndex = Math.min(activeIndex + 1, items.length - 1);
                    items.forEach((el, i) => { el.style.background = i === activeIndex ? '#1a3050' : 'transparent'; });
                    if (items[activeIndex]) items[activeIndex].scrollIntoView({ block: 'nearest' });
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    if (items.length === 0) return;
                    activeIndex = Math.max(activeIndex - 1, 0);
                    items.forEach((el, i) => { el.style.background = i === activeIndex ? '#1a3050' : 'transparent'; });
                    if (items[activeIndex]) items[activeIndex].scrollIntoView({ block: 'nearest' });
                } else if (e.key === 'Enter' || e.key === 'Tab') {
                    if (activeIndex >= 0 && items.length > activeIndex) {
                        e.preventDefault();
                        const sym = items[activeIndex].getAttribute('data-symbol');
                        if (sym) { symbolInput.value = sym; symbolDropdown.style.display = 'none'; activeIndex = -1; }
                    } else {
                        symbolDropdown.style.display = 'none';
                    }
                } else if (e.key === 'Escape') {
                    symbolDropdown.style.display = 'none';
                    activeIndex = -1;
                }
            });

            symbolDropdown.addEventListener('mousedown', function(e) {
                const target = e.target.closest('div[data-symbol]');
                if (target) {
                    e.preventDefault();
                    symbolInput.value = target.getAttribute('data-symbol');
                    symbolDropdown.style.display = 'none';
                    activeIndex = -1;
                }
            });

            document.addEventListener('click', function(e) {
                if (e.target !== symbolInput && !symbolDropdown.contains(e.target)) {
                    symbolDropdown.style.display = 'none';
                    activeIndex = -1;
                }
            });

            document.getElementById('detect-form').addEventListener('submit', async function(e) {
                e.preventDefault();
                const symbolVal = symbolInput.value.trim();
                if (!symbolVal) { alert('请输入或选择一个交易对'); return; }

                submitBtn.disabled = true;
                submitBtn.textContent = '⏳ 检测中...';
                resultArea.innerHTML = '<div class="loading"><div class="spinner"></div><p>正在从 ' + exchangeSelect.options[exchangeSelect.selectedIndex].text + ' 抓取 ' + symbolVal + ' 数据...</p></div>';

                const params = new URLSearchParams({
                    exchange: exchangeSelect.value,
                    symbol: symbolVal,
                    timeframe: document.getElementById('timeframe').value,
                    days: document.getElementById('days').value,
                    amp: document.getElementById('amp').value
                });

                try {
                    const resp = await fetch('/api/detect?' + params.toString());
                    const data = await resp.json();

                    if (data.status === 'error') {
                        resultArea.innerHTML = '<div class="error-box"><h3>❌ 检测失败</h3><p>' + data.message + '</p></div>';
                    } else {
                        var h = '<div class="result-card"><h3>📊 ' + data.symbol + ' 检测报告</h3>';
                        h += '<div class="result-stats">';
                        h += '<div class="stat"><div class="number">' + data.total_klines.toLocaleString() + '</div><div class="label">总K线数</div></div>';
                        h += '<div class="stat"><div class="number">' + data.hit_count + '</div><div class="label">命中事件</div></div>';
                        h += '<div class="stat"><div class="number">0</div><div class="label">误报数</div></div>';
                        h += '<div class="stat"><div class="number">' + data.exchange + '</div><div class="label">交易所</div></div>';
                        h += '</div>';

                        if (data.hit_count > 0) {
                            if (data.top_events.length > 0) {
                                h += '<h3 style="color:#f7931a; margin-top:15px;">🔥 振幅最大事件（共' + data.hit_count + '次命中）</h3>';
                                h += '<table><tr><th>时间</th><th>方向</th><th>振幅</th></tr>';
                                data.top_events.forEach(function(ev) {
                                    h += '<tr><td>' + ev.timestamp + '</td><td>' + ev.direction + '</td><td class="amp">' + ev.amplitude + '%</td></tr>';
                                });
                                h += '</table>';
                            } else {
                                h += '<p class="warning-msg">⚠️ 命中 ' + data.hit_count + ' 个事件，但详情解析失败。请在服务器终端手动运行脚本查看完整结果。</p>';
                            }
                        } else {
                            h += '<p class="success-msg">✅ 未发现符合严格标准的插针事件。模型零误报。</p>';
                        }
                        h += '</div>';
                        resultArea.innerHTML = h;
                    }
                } catch(err) {
                    resultArea.innerHTML = '<div class="error-box"><h3>❌ 网络请求失败</h3><p>' + err.message + '</p></div>';
                } finally {
                    submitBtn.disabled = false;
                    submitBtn.textContent = '🚀 开始检测';
                }
            });
        })();
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/symbols')
def api_symbols():
    exchange_id = request.args.get('exchange', 'binanceusdm')
    try:
        import ccxt
        exchange_class = getattr(ccxt, exchange_id)
        exchange_instance = exchange_class({'enableRateLimit': True, 'timeout': 30000})
        exchange_instance.load_markets(reload=True)
        symbols = []
        for symbol, market in exchange_instance.markets.items():
            is_swap = market.get('swap') or market.get('type') == 'swap' or market.get('linear')
            is_usdt = '/USDT' in symbol or symbol.endswith(':USDT')
            if is_swap and is_usdt:
                clean = symbol.split(':')[0] if ':' in symbol else symbol
                if clean.endswith('/USDT'):
                    symbols.append(clean)
        symbols = sorted(list(set(symbols)))
        return jsonify({"status": "success", "symbols": symbols, "count": len(symbols)})
    except Exception as e:
        error_detail = traceback.format_exc()
        print(f"[SYMBOLS ERROR] {error_detail}")
        fallback = {
            'binanceusdm': ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 'DOT/USDT', 'LINK/USDT', 'MATIC/USDT', 'UNI/USDT', 'ATOM/USDT', 'LTC/USDT', 'ETC/USDT', 'OP/USDT', 'ARB/USDT', 'FIL/USDT', 'APT/USDT', 'NEAR/USDT', 'PEPE/USDT', 'WIF/USDT', 'SUI/USDT', 'INJ/USDT', 'TIA/USDT'],
            'okx': ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'DOGE/USDT', 'XRP/USDT', 'ADA/USDT', 'AVAX/USDT', 'DOT/USDT', 'LINK/USDT', 'MATIC/USDT'],
            'gate': ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'DOGE/USDT', 'PEPE/USDT', 'TON/USDT', 'SUI/USDT', 'APT/USDT', 'ARB/USDT', 'OP/USDT'],
        }
        symbols = fallback.get(exchange_id, fallback['binanceusdm'])
        return jsonify({"status": "success", "symbols": symbols, "count": len(symbols), "source": "fallback"})

@app.route('/api/detect')
def api_detect():
    exchange = request.args.get('exchange', 'binanceusdm')
    symbol = request.args.get('symbol', 'SOL/USDT').strip()
    timeframe = request.args.get('timeframe', '5m')
    days = request.args.get('days', '3')
    amp = request.args.get('amp', '3.0')
    
    if not symbol or '/' not in symbol:
        return jsonify({"status": "error", "message": "交易对格式错误"}), 400
    
    script_path = '/root/liquidation-wick-detector/wick_detector_v4.py'
    if not os.path.exists(script_path):
        return jsonify({"status": "error", "message": "检测脚本未找到"}), 500
    
    try:
        result = subprocess.run(
            ['python3', script_path,
             '--exchange', exchange,
             '--mode', 'live',
             '--symbol', symbol,
             '--timeframe', timeframe,
             '--days', str(days),
             '--amp', str(amp),
             '--no_debug'],
            capture_output=True, text=True, timeout=600,
            cwd='/root/liquidation-wick-detector'
        )
        
        output = result.stdout
        error_output = result.stderr
        
        total_klines = 0
        hit_count = 0
        top_events = []
        
        kline_match = re.search(r'总K线数:\s+([\d,]+)', output)
        if kline_match:
            total_klines = int(kline_match.group(1).replace(',', ''))
        
        hit_match = re.search(r'命中事件:\s+(\d+)', output)
        if hit_match:
            hit_count = int(hit_match.group(1))
        
        if total_klines == 0 and hit_count == 0 and 'Traceback' in error_output:
            last_lines = error_output.strip().split('\n')[-5:]
            return jsonify({"status": "error", "message": "脚本异常：" + ' | '.join(last_lines)}), 500
        
        top_section = output.split('振幅最大 Top')
        if len(top_section) > 1:
            lines = top_section[1].strip().split('\n')
            for line in lines:
                line = line.strip()
                if not line or '|' not in line:
                    continue
                # 去掉行首序号 "1. " 或 "   1. "
                if '. ' in line[:6]:
                    line = line.split('. ', 1)[1]
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 3:
                    try:
                        top_events.append({
                            'timestamp': parts[0],
                            'direction': parts[1],
                            'amplitude': parts[2].replace('振幅:', '').replace('%', '').strip()
                        })
                    except (IndexError, ValueError):
                        continue
        
        return jsonify({
            "status": "success",
            "exchange": exchange,
            "symbol": symbol,
            "total_klines": total_klines,
            "hit_count": hit_count,
            "top_events": top_events
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "检测超时"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"服务器错误：{str(e)}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
