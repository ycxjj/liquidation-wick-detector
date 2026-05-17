#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wick Detector Web：每日热门榜日报 + 自选合约检测
"""
import os
import re
import subprocess
import threading
import uuid
import time
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, render_template_string, render_template, request, session

import daily_scan
import points_system
import redis_rate_limit

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "wick-detector-dev-secret-change-me")
app.config['PERMANENT_SESSION_LIFETIME'] = 86400 * 30  # 30天
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get(
    'SESSION_COOKIE_SECURE', ''
).lower() in ('1', 'true', 'yes')
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_CONTENT_LENGTH', 2 * 1024 * 1024))
ADMIN_PATH = "/" + os.environ.get("ADMIN_PATH", "admin").strip("/")
_SYMBOLS_CACHE: dict = {}
_SYMBOLS_CACHE_TTL = int(os.environ.get('SYMBOLS_CACHE_TTL', '300'))


def client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    return redis_rate_limit.rate_limit(key, limit, window_seconds)


def admin_audit(action: str, target: str = None, detail: dict = None):
    if not is_points_admin():
        return
    points_system.log_admin_audit(
        actor_wallet=session.get("wallet_address", "session_admin"),
        action=action,
        target=target,
        detail=detail,
        ip_address=client_ip(),
    )


@app.before_request
def basic_rate_guard():
    if app.config.get('TESTING'):
        return
    ip = client_ip()
    path = request.path
    if path.startswith('/api/points/login') or path.startswith('/api/points/nonce'):
        if not rate_limit(f"auth:{ip}", 20, 300):
            return jsonify({"error": "请求过于频繁，请稍后再试"}), 429
    elif path.startswith('/api/detect'):
        if not rate_limit(f"detect:{ip}", 60, 300):
            return jsonify({"status": "error", "message": "检测请求过于频繁，请稍后再试"}), 429
    elif path.startswith('/api/'):
        if not rate_limit(f"api:{ip}", 240, 300):
            return jsonify({"error": "请求过于频繁，请稍后再试"}), 429


@app.after_request
def add_security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    resp.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
    if request.is_secure:
        resp.headers.setdefault('Strict-Transport-Security', 'max-age=15552000; includeSubDomains')
    return resp


def is_points_admin():
    """积分系统管理员判断：后台 session 或 ADMIN_ADDRESSES 钱包地址任一满足即可"""
    if session.get("is_admin"):
        return True
    wallet_address = session.get("wallet_address", "").lower()
    admin_addresses = [x.strip().lower() for x in os.environ.get("ADMIN_ADDRESSES", "").split(",") if x.strip()]
    return bool(wallet_address and wallet_address in admin_addresses)

# 任务管理 - 使用文件存储
TASKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'tasks')
os.makedirs(TASKS_DIR, exist_ok=True)

def save_task(task_id, task_data):
    """保存任务到文件"""
    task_file = os.path.join(TASKS_DIR, f"{task_id}.json")
    with open(task_file, 'w') as f:
        json.dump(task_data, f)

def load_task(task_id):
    """从文件加载任务"""
    task_file = os.path.join(TASKS_DIR, f"{task_id}.json")
    if not os.path.exists(task_file):
        return None
    with open(task_file, 'r') as f:
        return json.load(f)

def run_detect_task(task_id, ex, sym, tf, days, amp):
    """后台运行检测任务：直接调用检测引擎，避免子进程卡住"""
    try:
        task = load_task(task_id)
        task['status'] = 'running'
        task['updated_at'] = time.time()
        save_task(task_id, task)

        from wick_detector_v4 import LiquidationDetector, get_default_amplitude

        try:
            days_int = max(1, min(int(days), 7))
        except Exception:
            days_int = 1
        try:
            amp_float = float(amp) if amp not in (None, "") else get_default_amplitude(sym)
        except Exception:
            amp_float = get_default_amplitude(sym)

        detector = LiquidationDetector(exchange_name=ex, symbol=sym)
        df = detector.fetch_ohlcv_live(timeframe=tf, days_back=days_int, market_type='swap')
        if df is None or df.empty:
            task['status'] = 'error'
            task['error'] = '未能获取K线数据：交易所接口无数据、交易对不支持，或服务器到交易所网络异常'
            task['updated_at'] = time.time()
            save_task(task_id, task)
            return

        df_scored = detector.detect_wicks(
            df,
            min_amplitude_pct=amp_float,
            body_ratio_threshold=0.5,
            wick_ratio_threshold=5.0,
            rebound_threshold=0.7,
        )
        hits = df_scored[df_scored['wick_score'] >= 0.8].copy() if 'wick_score' in df_scored.columns else df_scored.iloc[0:0]

        top_events = []
        if not hits.empty:
            sorted_hits = hits.sort_values('amplitude', ascending=False)
            for _, row in sorted_hits.iterrows():
                top_events.append({
                    "timestamp": str(row.get('timestamp', '')),
                    "direction": str(row.get('direction', '')),
                    "amplitude": f"{float(row.get('amplitude', 0)):.2f}",
                })

        task['status'] = 'completed'
        task['result'] = {
            "status": "success",
            "exchange": ex,
            "symbol": sym,
            "total_klines": int(len(df_scored)),
            "hit_count": int(len(hits)),
            "top_events": top_events
        }
        task['updated_at'] = time.time()
        save_task(task_id, task)
    except Exception as e:
        task = load_task(task_id) or {}
        task['status'] = 'error'
        msg = str(e) or type(e).__name__
        lower_msg = msg.lower()
        if any(x in lower_msg for x in ['403', '451', 'forbidden', 'restricted location', 'cloudfront', 'access denied']):
            msg = '交易所接口拒绝访问，服务器 IP 可能被地区限制或风控拦截。原始错误：' + msg[-500:]
        elif any(x in lower_msg for x in ['ratelimit', 'rate limit', 'ddosprotection', 'too many requests', '429']):
            msg = '交易所接口触发限流，请稍后重试或减少频率。原始错误：' + msg[-500:]
        elif any(x in lower_msg for x in ['requesttimeout', 'timeout', 'timed out']):
            msg = '交易所接口请求超时，可能是服务器网络到该交易所不稳定或被限速。原始错误：' + msg[-500:]
        task['error'] = msg[-1000:]
        save_task(task_id, task)
    finally:
        task = load_task(task_id) or {}
        task['updated_at'] = time.time()
        save_task(task_id, task)


HTML = r'''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Wick Detector - 插针检测引擎</title>
    <link rel="icon" type="image/png" href="/logo.png?v=4">
    <style>
:root{--bg:#070b1a;--card:#10192e;--border:#1e3a5f;--accent:#00d4ff;--muted:#8892b0;--text:#e8edf7;--danger:#f44;--ok:#3ecf8e}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--text);min-height:100vh;line-height:1.45}
.wrap{max-width:1100px;margin:0 auto;padding:28px 18px 60px}
.hero{text-align:center;margin-bottom:28px}
.hero h1{font-size:1.85rem;color:var(--accent);letter-spacing:-.02em}
.hero p{color:var(--muted);font-size:.95rem;margin-top:8px}
.tabs{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-bottom:22px}
.tab{background:var(--card);border:1px solid var(--border);color:var(--muted);padding:10px 22px;border-radius:10px;cursor:pointer;font-weight:600;font-size:.92rem}
.tab.on{border-color:var(--accent);color:var(--accent);box-shadow:0 0 0 1px rgba(0,212,255,.15)}
.panel{display:none}
.panel.show{display:block}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:22px 20px;margin-bottom:18px}
.card h2{font-size:1.05rem;color:var(--accent);margin-bottom:14px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.badge{font-size:.72rem;font-weight:700;padding:3px 10px;border-radius:999px;background:#1a3050;color:var(--accent)}
.badge.run{background:#2a1a10;color:#ffb020}
.meta{color:var(--muted);font-size:.82rem;margin-bottom:14px;line-height:1.6}
.toolbar{margin-bottom:14px;display:flex;flex-direction:column;gap:10px}
.toolbar-actions{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.toolbar-filter{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:.88rem;padding:11px 14px;background:#0a1020;border:1px solid var(--border);border-radius:9px;width:100%;cursor:pointer;-webkit-tap-highlight-color:transparent}
.toolbar-filter input{width:20px;height:20px;margin:0;flex-shrink:0;accent-color:var(--accent)}
.btn{background:var(--accent);color:#042;font-weight:700;border:none;padding:9px 16px;border-radius:9px;cursor:pointer;font-size:.88rem}
.btn:disabled{opacity:.45;cursor:not-allowed;background:#3a4a5f;color:#8892b0}
.btn.ghost{background:transparent;border:1px solid var(--border);color:var(--muted)}
.btn-wait-hint{color:var(--muted);font-size:.8rem;text-align:center;margin-top:8px;display:none}
.date-bar{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-bottom:14px;padding:12px 14px;background:#0a1020;border-radius:10px;border:1px solid var(--border)}
.date-bar .form-group{margin-bottom:0;min-width:160px}
.date-bar label{font-size:.8rem}
.date-bar input[type=date],.date-bar select{padding:8px 10px;font-size:.88rem}
.view-hint{font-size:.82rem;color:var(--muted);margin-top:8px}
.table-wrap{overflow-x:auto;border-radius:10px;border:1px solid var(--border)}
table{width:100%;border-collapse:collapse;font-size:.86rem}
th{text-align:left;padding:10px 12px;color:var(--muted);border-bottom:2px solid var(--border);white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid rgba(30,58,95,.6);vertical-align:top}
tr.row-hit{background:rgba(0,212,255,.06)}
td.num{font-variant-numeric:tabular-nums}
td.amp{color:#f7931a;font-weight:600}
.sym{font-weight:600;color:var(--text)}
.empty{text-align:center;padding:36px 16px;color:var(--muted)}
.err{color:#888;font-size:.8rem;cursor:help}
.form-row{display:flex;gap:12px;flex-wrap:wrap}
.form-group{flex:1;min-width:150px;margin-bottom:12px}
label{display:block;margin-bottom:5px;color:var(--muted);font-size:.85rem}
select,input{width:100%;padding:10px 12px;border-radius:9px;border:1px solid var(--border);background:#0a1020;color:var(--text)}
select:focus,input:focus{outline:none;border-color:var(--accent)}
.btn.block{width:100%;padding:12px;margin-top:4px}
.hint{color:var(--muted);font-size:.82rem;margin-top:10px;line-height:1.5}
.result-card{background:#0a1020;border-radius:10px;padding:18px;margin-top:14px;border:1px solid var(--border)}
.result-stats{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}
.stat{background:var(--card);padding:12px;border-radius:9px;text-align:center;flex:1;min-width:72px}
.stat .n{font-size:1.5rem;font-weight:800;color:var(--accent)}
.stat .lb{font-size:.72rem;color:var(--muted)}
.loading{text-align:center;padding:28px;color:var(--muted)}
.spinner{border:3px solid var(--border);border-top-color:var(--accent);border-radius:50%;width:40px;height:40px;animation:sp 1s linear infinite;margin:0 auto 12px}
@keyframes sp{to{transform:rotate(360deg)}}
.progress-wrap{max-width:360px;margin:14px auto 0}
.progress-track{height:9px;background:#1a2540;border-radius:6px;overflow:hidden}
.progress-indet{height:100%;width:36%;background:linear-gradient(90deg,transparent,var(--accent),transparent);animation:ind 1.2s ease-in-out infinite}
@keyframes ind{0%{transform:translateX(-90%)}100%{transform:translateX(280%)}}
.elapsed-big{font-size:1.7rem;color:var(--accent);font-weight:800;margin-top:10px;font-variant-numeric:tabular-nums}
.dd{position:absolute;top:100%;left:0;right:0;background:var(--card);border:1px solid var(--accent);border-radius:0 0 9px 9px;max-height:260px;overflow-y:auto;z-index:99;display:none}
.dd div{padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border)}
.dd div:hover{background:#1a3050}
.error-box{background:#1a0a0a;border:1px solid var(--danger);border-radius:10px;padding:16px}
.disclaimer{text-align:center;color:#555;font-size:.76rem;margin-top:36px;padding-top:20px;border-top:1px solid var(--border)}
.details{font-size:.78rem;color:var(--muted);margin-top:6px}
details.hit-detail{margin-top:6px}
.hit-detail summary{cursor:pointer;color:var(--accent);padding:4px 0;-webkit-tap-highlight-color:transparent}
.daily-cards{display:none}
.daily-row-card{background:#0a1020;border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:10px}
.daily-row-card.hit{border-color:rgba(0,212,255,.35);background:rgba(0,212,255,.06)}
.drc-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.drc-rank{color:var(--muted);font-size:.8rem;min-width:28px}
.drc-sym{font-weight:700;color:var(--text);flex:1;word-break:break-all}
.drc-badge{font-size:.75rem;color:var(--accent);background:rgba(0,212,255,.12);padding:4px 10px;border-radius:6px;white-space:nowrap}
.drc-meta{font-size:.8rem;color:var(--muted);line-height:1.55;margin-bottom:8px}
.drc-err{color:#f88;font-size:.8rem;margin:6px 0}
.drc-hit-btn{width:100%;margin-top:4px;padding:10px}
.hit-modal-list{max-height:55vh;overflow-y:auto;-webkit-overflow-scrolling:touch;text-align:left;margin:0 0 16px;font-size:.88rem}
.hit-modal-list .hit-line{padding:10px 0;border-bottom:1px solid var(--border);line-height:1.5}
.hit-modal-list .hit-line:last-child{border-bottom:none}
.hit-events-title{color:var(--accent);font-size:.95rem;margin:14px 0 10px;font-weight:600}
.hit-event-cards{display:none}
.hit-event-card{background:#0a1020;border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:10px}
.hit-event-card .hev-idx{color:var(--muted);font-size:.75rem;margin-bottom:8px}
.hit-event-card .hev-row{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;padding:7px 0;border-bottom:1px solid rgba(30,58,95,.45);font-size:.86rem;line-height:1.45}
.hit-event-card .hev-row:last-child{border-bottom:none}
.hit-event-card .hev-label{color:var(--muted);flex-shrink:0;min-width:42px}
.hit-event-card .hev-val{text-align:right;word-break:break-word;flex:1}
.detect-hit-desk{margin-top:4px}
.ex-tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.ex-tab{background:#0a1020;border:1px solid var(--border);color:var(--muted);padding:8px 16px;border-radius:9px;cursor:pointer;font-size:.85rem;font-weight:600}
.ex-tab.on{border-color:var(--accent);color:var(--accent)}
.modal-mask{display:none;position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:1000;align-items:center;justify-content:center;padding:20px}
.modal-mask.show{display:flex}
.modal-card{background:var(--card);border:1px solid var(--accent);border-radius:16px;padding:26px 22px;max-width:440px;width:100%;text-align:center;box-shadow:0 24px 80px rgba(0,0,0,.55)}
.modal-card p{color:var(--text);font-size:.93rem;line-height:1.6;margin-bottom:20px;white-space:pre-wrap}
.top-nav{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
.top-link{color:var(--accent);text-decoration:none;display:inline-flex;align-items:center;gap:4px;font-size:.9rem;border:1px solid var(--border);padding:7px 12px;border-radius:9px;background:rgba(16,25,46,.7)}
.top-link:hover{border-color:var(--accent);background:rgba(0,212,255,.08)}
@media (max-width: 720px){
  .wrap{padding:14px 10px 38px}
  .hero{margin-bottom:18px}
  .hero h1{font-size:1.45rem}
  .hero p{font-size:.82rem}
  .top-nav{gap:8px;margin-bottom:14px}
  .top-link{font-size:.82rem;padding:7px 10px}
  .tabs{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px}
  .tab{padding:10px 8px;font-size:.82rem}
  .card{padding:16px 12px;border-radius:12px}
  .toolbar{align-items:center}
  .toolbar-actions{display:flex;flex-direction:column;align-items:center;width:100%;gap:10px}
  .toolbar-actions .btn{width:100%;max-width:320px;margin:0 auto!important;text-align:center}
  .toolbar-filter{width:100%;max-width:320px;justify-content:center;margin:0 auto}
  .daily-table-desk{display:none!important}
  .daily-cards{display:block!important}
  .hit-event-cards{display:block!important}
  .detect-hit-desk{display:none!important}
  .result-card table{min-width:0;width:100%}
  .date-bar{display:grid;grid-template-columns:1fr;padding:10px;gap:8px}
  .date-bar .form-group{min-width:0;width:100%}
  .ex-tabs{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
  .ex-tab{padding:9px 6px;font-size:.8rem}
  .form-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  .form-group{min-width:0;margin-bottom:0}
  .form-group:nth-child(2){grid-column:1 / -1}
  label{font-size:.78rem}
  select,input{padding:10px 9px;font-size:16px}
  .hint,.meta,.view-hint{font-size:.76rem}
  .result-card{padding:14px 10px;overflow:hidden}
  .result-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
  .stat{min-width:0;padding:10px 6px}
  .stat .n{font-size:1.25rem}
  table{font-size:.78rem;min-width:640px}
  th,td{padding:8px 9px}
  .table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
  #dailyMetaEx{word-break:break-word}
}
@media (max-width: 420px){
  .form-row{grid-template-columns:1fr}
  .form-group:nth-child(2){grid-column:auto}
  .tabs{grid-template-columns:1fr}
  .result-stats{grid-template-columns:1fr}
}
    </style>
</head>
<body>
<div id="modalMask" class="modal-mask" role="dialog" aria-modal="true">
  <div class="modal-card">
    <p id="modalMsg"></p>
    <button type="button" class="btn block" id="modalOk">知道了</button>
  </div>
</div>
<div id="hitModalMask" class="modal-mask" role="dialog" aria-modal="true">
  <div class="modal-card" style="max-width:520px;text-align:left">
    <h3 id="hitModalTitle" style="color:var(--accent);margin:0 0 8px;font-size:1.05rem"></h3>
    <div id="hitModalList" class="hit-modal-list"></div>
    <button type="button" class="btn block" id="hitModalOk">关闭</button>
  </div>
</div>
<div class="wrap">
  <div class="top-nav">
    <a href="/" class="top-link">← 返回首页</a>
    <a href="/points" class="top-link" id="backPointsLink">← 返回积分系统</a>
  </div>
  <div class="hero">
    <h1>Wick Detector</h1>
    <p>每日热门合约插针日报 · 自选永续合约检测</p>
  </div>
  <div class="tabs">
    <button type="button" class="tab on" id="tabP1" data-p="p1">📅 昨日热门 Top100 日报</button>
    <button type="button" class="tab" id="tabP2" data-p="p2">自选合约检测</button>
  </div>

  <div id="p1" class="panel show">
    <div class="card">
      <h2>热门榜插针概览 <span class="badge" id="jobBadge">—</span></h2>
      <p class="meta">每日 <strong>08:00（Asia/Shanghai）</strong> 依次对 <strong>币安 / 欧易 / Gate / Bybit / MEXC / Bitget</strong> 六家 USDT 永续市场分别取 <strong>24h 成交额 Top100</strong>，
      对<strong>前一自然日</strong>整日 K 线做插针扫描（规则与下方自选一致）。下方分 Tab 查看各所数据；首次可手动触发生成。</p>
      <div class="toolbar">
        <div class="toolbar-actions">
          <button type="button" class="btn ghost" id="btnRefreshDaily">刷新数据</button>
          {% if is_admin %}<button type="button" class="btn" id="btnRunDaily">后台生成昨日日报（六所）</button>{% endif %}
        </div>
        <label class="toolbar-filter"><input type="checkbox" id="onlyHits"> 仅显示有插针命中</label>
      </div>
      <div class="date-bar">
        <div class="form-group"><label for="dailyDatePick">按已有存档选日期</label>
          <select id="dailyDatePick"><option value="">最新（各所各自最近一条）</option></select></div>
        <div class="form-group"><label for="dailyDateManual">或指定日历日</label>
          <input type="date" id="dailyDateManual" title="查询该数据日的三所日报"></div>
        <button type="button" class="btn ghost" id="btnQueryDate">查询</button>
      </div>
      <p id="dailyViewHint" class="view-hint"></p>
      <p id="jobStatusHint" class="meta" style="display:none;color:#ffb020;font-weight:600"></p>
      <div class="ex-tabs" id="exTabs">
        <button type="button" class="ex-tab on" data-ex="binanceusdm">币安 USDM</button>
        <button type="button" class="ex-tab" data-ex="okx">欧易</button>
        <button type="button" class="ex-tab" data-ex="gate">Gate.io</button>
        <button type="button" class="ex-tab" data-ex="bybit">Bybit</button>
        <button type="button" class="ex-tab" data-ex="mexc">MEXC</button>
        <button type="button" class="ex-tab" data-ex="bitget">Bitget</button>
      </div>
      <div id="dailyMetaEx" class="meta"></div>
      <div id="dailyBody"><div class="empty">加载中…</div></div>
    </div>
  </div>

  <div id="p2" class="panel">
    <div class="card">
      <h2>自选合约检测</h2>
      <form id="f" autocomplete="off">
        <div class="form-row">
          <div class="form-group"><label>交易所</label>
            <select id="ex"><option value="binanceusdm" selected>币安</option><option value="okx">欧易</option><option value="gate">Gate.io</option><option value="bybit">Bybit</option><option value="mexc">MEXC</option><option value="bitget">Bitget</option></select></div>
          <div class="form-group" style="position:relative"><label>交易对</label>
            <input type="text" id="sym" placeholder="加载中…" autocomplete="off"><div id="dd" class="dd"></div></div>
          <div class="form-group"><label>K线周期</label>
            <select id="tf"><option value="1m">1分</option><option value="5m">5分</option><option value="15m" selected>15分</option><option value="1h">1时</option></select></div>
          <div class="form-group"><label>回溯天数</label>
            <input type="number" id="d" value="1" min="1" max="30"></div>
          <div class="form-group"><label>振幅%</label>
            <input type="number" id="a" value="3.0" min="0.5" max="20" step="0.5"></div>
        </div>
        <button type="submit" class="btn block" id="btn" disabled>加载合约中…</button>
        <p class="btn-wait-hint" id="btnWaitHint">正在加载默认交易对，加载完成后才能开始检测</p>
        <p class="hint">拉取交易所数据并本地扫描，<strong>推荐使用 15分钟 K线 + 1天数据；最大支持 30 天回溯</strong>。<br>
        长回溯会自动分页拉取，数据不足时会自动 ccxt 兜底并在结果中提示覆盖率。<br>
        检测期间请勿刷新页面，耐心等待。</p>
      </form>
      <div id="res"></div>
    </div>
  </div>

  <p class="disclaimer">仅供教育、科研和技术演示目的使用</p>
</div>
<script>
(function(){
var sym=document.getElementById('sym'),dd=document.getElementById('dd'),ex=document.getElementById('ex'),btn=document.getElementById('btn'),res=document.getElementById('res');
var all=[],loading=false,symReady=false,pollT=null,prevJobRunning=false;
var EX_NAMES={binanceusdm:'币安 USDM',okx:'欧易',gate:'Gate.io',bybit:'Bybit',mexc:'MEXC',bitget:'Bitget'};
var __reports=null,__dailyRows=[],currentDailyEx='binanceusdm';

function showModal(msg){
  document.getElementById('modalMsg').textContent=msg||'';
  document.getElementById('modalMask').classList.add('show');
}
document.getElementById('modalOk').onclick=function(){
  document.getElementById('modalMask').classList.remove('show');
};
document.getElementById('modalMask').onclick=function(e){
  if(e.target.id==='modalMask')document.getElementById('modalMask').classList.remove('show');
};
document.getElementById('hitModalOk').onclick=function(){
  document.getElementById('hitModalMask').classList.remove('show');
};
document.getElementById('hitModalMask').onclick=function(e){
  if(e.target.id==='hitModalMask')document.getElementById('hitModalMask').classList.remove('show');
};
function escHtml(s){
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
}
function showHitModal(symbol,hits){
  var title=document.getElementById('hitModalTitle');
  var list=document.getElementById('hitModalList');
  title.textContent=(symbol||'合约')+' · 插针详情（'+hits.length+' 条）';
  list.innerHTML=hits.map(function(e,i){
    return '<div class="hit-line"><strong>'+(i+1)+'.</strong> '+escHtml(e.timestamp)+' · '+escHtml(e.direction)+' · <span class="amp">'+escHtml(e.amplitude)+'%</span></div>';
  }).join('');
  document.getElementById('hitModalMask').classList.add('show');
}
function setDetectBtnReady(ready,hint){
  symReady=!!ready;
  btn.disabled=!symReady;
  var wh=document.getElementById('btnWaitHint');
  if(symReady){
    btn.textContent='开始检测';
    if(wh)wh.style.display='none';
  }else{
    btn.textContent='加载合约中…';
    if(wh){wh.style.display='block';wh.textContent=hint||'正在加载默认交易对…';}
  }
}
setDetectBtnReady(false,'正在加载默认交易对，请稍候…');

function tab(id){
  document.querySelectorAll('.tab').forEach(function(t){t.classList.toggle('on',t.getAttribute('data-p')===id);});
  document.getElementById('p1').classList.toggle('show',id==='p1');
  document.getElementById('p2').classList.toggle('show',id==='p2');
  if(id==='p1'){fillDatePick();loadDaily();startPoll();}else{stopPoll();}
}
document.getElementById('tabP1').onclick=function(){tab('p1');};
document.getElementById('tabP2').onclick=function(){tab('p2');};

// 初始化：检查 URL 参数决定显示哪个 Tab
var urlParams=new URLSearchParams(window.location.search);
var initialTab=urlParams.get('tab')||'p2'; // 默认显示自选检测
tab(initialTab);

function stopPoll(){if(pollT){clearTimeout(pollT);pollT=null;}}
function startPoll(){
  stopPoll();
  function tick(){
    fetch('/api/daily/status').then(function(r){return r.json();}).then(function(d){
      var el=document.getElementById('jobBadge'),hint=document.getElementById('jobStatusHint');
      if(!el)return;
      if(prevJobRunning && !d.running){fillDatePick();loadDaily();}
      prevJobRunning=!!d.running;
      if(d.running){
        var px=d.phase_index?' '+d.phase_index+' ':' ';
        var pl=d.phase_label||'';
        el.textContent='扫描中'+px+(pl?pl+' ':'')+d.progress;
        el.className='badge run';
        if(hint){
          hint.style.display='block';
          hint.textContent='当前进度：'+(pl||'交易所')+' '+d.progress+(d.phase_index?' · 轮次 '+d.phase_index:'')+' · 三所依次执行，耗时可较长；完成后自动刷新，也可点「刷新数据」。';
        }
      }else{
        el.textContent='就绪';
        el.className='badge';
        if(hint)hint.style.display='none';
        if(d.last_error){
          el.textContent='上次异常';
          el.title='点击查看详情';
          el.style.cursor='pointer';
          el.onclick=function(){showModal('上次后台生成日报时发生错误：\n\n'+d.last_error);};
        }else{
          el.style.cursor='default';
          el.onclick=null;
        }
      }
    }).catch(function(){}).finally(function(){
      if(document.getElementById('p1').classList.contains('show'))
        pollT=setTimeout(tick,4000);
    });
  }
  tick();
}

function fmtVol(v){
  if(v>=1e9)return(v/1e9).toFixed(2)+'B';
  if(v>=1e6)return(v/1e6).toFixed(2)+'M';
  if(v>=1e3)return(v/1e3).toFixed(1)+'K';
  return String(Math.round(v||0));
}

function buildTable(rows){
  __dailyRows=rows;
  return buildTableMobileCards(rows)+buildTableLegacy(rows);
}
function buildTableLegacy(rows){
  var h='<div class="table-wrap daily-table-desk"><table><thead><tr><th>#</th><th>合约</th><th>24h额</th><th>K线</th><th>命中</th><th>最大振幅</th><th>摘要</th></tr></thead><tbody>';
  rows.forEach(function(r){
    var hit=r.hit_count>0;
    h+='<tr class="'+(hit?'row-hit':'')+'"><td class="num">'+r.rank_vol+'</td><td class="sym">'+r.symbol+'</td><td class="num">'+fmtVol(r.quote_volume)+'</td>';
    h+='<td class="num">'+(r.total_klines||0)+'</td><td class="num">'+(r.hit_count||0)+'</td><td class="amp">'+(r.max_amplitude!=null?r.max_amplitude+'%':'—')+'</td><td>';
    if(r.error){h+='<span class="err" title="'+r.error.replace(/"/g,'&quot;')+'">数据异常</span>';}
    else if(r.hits&&r.hits.length){
      h+='<details class="hit-detail"><summary>'+r.hits.length+' 条事件</summary><div class="details">';
      r.hits.forEach(function(e){h+=e.timestamp+' '+e.direction+' '+e.amplitude+'%<br>';});
      h+='</div></details>';
    }else{h+='—';}
    h+='</td></tr>';
  });
  return h+'</tbody></table></div>';
}
function buildTableMobileCards(rows){
  var cards='';
  rows.forEach(function(r,idx){
    var hit=r.hit_count>0;
    cards+='<div class="daily-row-card'+(hit?' hit':'')+'">';
    cards+='<div class="drc-top"><span class="drc-rank">#'+r.rank_vol+'</span><span class="drc-sym">'+escHtml(r.symbol)+'</span>';
    if(hit)cards+='<span class="drc-badge">命中 '+r.hit_count+'</span>';
    cards+='</div><div class="drc-meta">24h '+fmtVol(r.quote_volume)+' · K线 '+(r.total_klines||0);
    if(r.max_amplitude!=null)cards+=' · 最大 <span class="amp">'+r.max_amplitude+'%</span>';
    cards+='</div>';
    if(r.error)cards+='<p class="drc-err">'+escHtml(r.error)+'</p>';
    else if(r.hits&&r.hits.length)cards+='<button type="button" class="btn ghost drc-hit-btn" data-hit-idx="'+idx+'">查看 '+r.hits.length+' 条插针详情</button>';
    cards+='</div>';
  });
  return '<div class="daily-cards">'+cards+'</div>';
}

function renderDailyTab(){
  var mb=document.getElementById('dailyMetaEx'),body=document.getElementById('dailyBody');
  if(!__reports){body.innerHTML='<div class="empty">无数据</div>';mb.textContent='';return;}
  var rep=__reports[currentDailyEx];
  if(!rep){
    mb.innerHTML='<strong>'+EX_NAMES[currentDailyEx]+'</strong>：暂无日报（尚未生成或该所扫描失败）';
    body.innerHTML='<div class="empty">该交易所暂无记录，请稍后刷新查看</div>';
    return;
  }
  mb.innerHTML='<strong>'+EX_NAMES[currentDailyEx]+'</strong> · 数据日 <strong>'+rep.report_date+'</strong> · 生成 '+rep.generated_at
    +' · 扫描 '+rep.symbol_count+' · <span style="color:#ffb020">命中 '+rep.with_hits_count+'</span>';
  var only=document.getElementById('onlyHits').checked;
  var rows=rep.rows.filter(function(r){return !only||r.hit_count>0;});
  if(rows.length===0){
    body.innerHTML='<div class="empty">当前筛选下无数据</div>';
    return;
  }
  body.innerHTML=buildTable(rows);
}

function renderDaily(data){
  var body=document.getElementById('dailyBody'),mb=document.getElementById('dailyMetaEx');
  if(!data||data.status==='error'){
    body.innerHTML='<div class="empty">'+(data&&data.message?data.message:'加载失败')+'</div>';
    mb.textContent='';
    __reports=null;
    return;
  }
  if(data.status==='empty'||!data.reports){
    __reports=null;
    var msg='暂无日报。请稍后刷新查看。';
    if(data.date){msg='数据日 <strong>'+data.date+'</strong> 尚无存档。请换一天或等待生成后在下拉中选择。';}
    body.innerHTML='<div class="empty">'+msg+'</div>';
    mb.textContent='';
    return;
  }
  __reports=data.reports;
  renderDailyTab();
}

function fillDatePick(){
  fetch('/api/daily/dates').then(function(r){return r.json();}).then(function(d){
    var sel=document.getElementById('dailyDatePick'),keep=sel.value;
    sel.innerHTML='<option value="">最新（各所各自最近一条）</option>';
    if(d.status==='ok'&&d.dates&&d.dates.length){
      d.dates.forEach(function(dt){
        var o=document.createElement('option');
        o.value=dt;o.textContent=dt;
        sel.appendChild(o);
      });
    }
    if(keep){
      var ok=false;
      for(var i=0;i<sel.options.length;i++){if(sel.options[i].value===keep){ok=true;break;}}
      if(ok)sel.value=keep;
    }
  }).catch(function(){});
}

function loadDaily(){
  var pick=document.getElementById('dailyDatePick');
  var manual=document.getElementById('dailyDateManual');
  var dsel=pick?pick.value:'';
  var dman=manual&&manual.value?manual.value:'';
  var url;
  var hint=document.getElementById('dailyViewHint');
  if(dman){
    url='/api/daily/snapshot?date='+encodeURIComponent(dman);
    if(hint)hint.textContent='正在查看数据日：'+dman+'（指定查询）';
  }else if(dsel){
    url='/api/daily/snapshot?date='+encodeURIComponent(dsel);
    if(hint)hint.textContent='正在查看数据日：'+dsel+'（库内存档）';
  }else{
    url='/api/daily/latest';
    if(hint)hint.textContent='正在查看：各交易所「最新一条」记录（未必同一天）';
  }
  fetch(url).then(function(r){return r.json();}).then(renderDaily).catch(function(){
    document.getElementById('dailyBody').innerHTML='<div class="empty">加载失败</div>';
  });
}
document.getElementById('btnRefreshDaily').onclick=function(){
  var b=document.getElementById('btnRefreshDaily');
  var orig=b.textContent;
  b.disabled=true;
  b.textContent='刷新中…';
  fillDatePick();
  loadDaily();
  setTimeout(function(){b.disabled=false;b.textContent=orig;},800);
};
document.getElementById('dailyDatePick').onchange=function(){
  document.getElementById('dailyDateManual').value='';
  loadDaily();
};
document.getElementById('btnQueryDate').onclick=function(){
  var v=document.getElementById('dailyDateManual').value;
  if(!v){showModal('请先选择或输入一个日期');return;}
  document.getElementById('dailyDatePick').value='';
  loadDaily();
};
document.getElementById('onlyHits').onchange=function(){renderDailyTab();};
document.getElementById('dailyBody').addEventListener('click',function(e){
  var b=e.target.closest('.drc-hit-btn');
  if(!b)return;
  var idx=parseInt(b.getAttribute('data-hit-idx'),10);
  var row=__dailyRows[idx];
  if(row&&row.hits&&row.hits.length)showHitModal(row.symbol,row.hits);
});
document.querySelectorAll('#exTabs .ex-tab').forEach(function(btn){
  btn.onclick=function(){
    document.querySelectorAll('#exTabs .ex-tab').forEach(function(b){b.classList.remove('on');});
    btn.classList.add('on');
    currentDailyEx=btn.getAttribute('data-ex');
    renderDailyTab();
  };
});
{% if is_admin %}
document.getElementById('btnRunDaily').onclick=function(){
  var b=document.getElementById('btnRunDaily');
  b.disabled=true;
  fetch('/api/daily/trigger',{method:'POST'}).then(function(r){return r.json().then(function(d){return {ok:r.ok,data:d};});}).then(function(x){
    var d=x.data;
    if(!x.ok){
      showModal((d&&d.message)||'没有权限');
      return;
    }
    if(d.started){
      showModal('已在后台开始依次扫描「币安 → 欧易 → Gate → Bybit → MEXC → Bitget」昨日热门 Top100。\n请勿重复点击；进度见标题旁徽章与本页橙色提示。');
      startPoll();
    }else{
      showModal(d.message||'未启动（可能已有任务在运行）');
    }
  }).finally(function(){b.disabled=false;});
};
{% endif %}

async function loadSym(){
  if(loading)return;
  loading=true;
  setDetectBtnReady(false,'正在加载 '+ex.options[ex.selectedIndex].text+' 合约列表…');
  sym.value='';
  sym.placeholder='加载中…';
  dd.style.display='none';
  try{
    var r=await fetch('/api/symbols?exchange='+encodeURIComponent(ex.value));
    var d=await r.json();
    if(d.status==='success'&&d.symbols&&d.symbols.length>0){
      all=d.symbols;
      sym.placeholder='搜索（'+d.count+'个合约）';
      var defaultSym=null;
      var priority=['SOL','BTC','ETH','DOGE','XRP','ADA','MATIC','AVAX','DOT','LINK'];
      var suffixes=['/USDT','/USD','/USDC'];
      for(var i=0;i<priority.length&&!defaultSym;i++){
        for(var j=0;j<suffixes.length;j++){
          var candidate=priority[i]+suffixes[j];
          if(all.indexOf(candidate)>=0){defaultSym=candidate;break;}
        }
      }
      if(!defaultSym){
        for(var k=0;k<all.length;k++){
          if(all[k].indexOf('PERP')!==0){defaultSym=all[k];break;}
        }
      }
      if(!defaultSym)defaultSym=all[0];
      sym.value=defaultSym;
      setDetectBtnReady(true);
    }else if(d.status==='error'){
      sym.placeholder='不支持';
      setDetectBtnReady(false,'加载失败：'+(d.message||'该交易所暂不可用'));
      showModal(d.message||'加载交易对失败');
    }else{
      sym.placeholder='加载失败';
      setDetectBtnReady(false,'未获取到合约列表，请换交易所或刷新');
    }
  }catch(e){
    sym.placeholder='加载失败';
    setDetectBtnReady(false,'网络异常，请刷新后重试');
    console.error('加载交易对异常:',e);
  }
  loading=false;
}
loadSym();
ex.addEventListener('change',loadSym);
function rd(m){if(!m.length){dd.innerHTML='<div style="padding:10px;color:#8892b0;text-align:center">无匹配</div>';}else{dd.innerHTML=m.map(function(s,i){return '<div data-s="'+s+'">'+s+'</div>';}).join('');}dd.style.display='block';}
function fs(q){if(!q)return all.slice(0,120);var qq=q.toUpperCase();return all.filter(function(s){return s.toUpperCase().indexOf(qq)>=0;}).slice(0,60);}
function sd(){if(!all.length)return;rd(fs(sym.value.trim()));}
sym.addEventListener('focus',sd);
sym.addEventListener('input',function(){sd();if(all.length&&sym.value.trim())setDetectBtnReady(true);});
dd.addEventListener('mousedown',function(e){var t=e.target.closest('div[data-s]');if(t){e.preventDefault();sym.value=t.getAttribute('data-s');dd.style.display='none';if(all.length)setDetectBtnReady(true);}});
document.addEventListener('click',function(e){if(e.target!==sym&&!dd.contains(e.target))dd.style.display='none';});

document.getElementById('f').addEventListener('submit',async function(e){
e.preventDefault();
if(!symReady){showModal('请等待合约列表加载完成后再开始检测');return;}
var sv=sym.value.trim();
if(!sv){showModal('请选择或输入交易对');return;}
var days=parseInt(document.getElementById('d').value)||1;
var tf=document.getElementById('tf').value;
var elapsedTimer=null,sec=0,taskSubmitted=false;
function finishDetection(){
  if(elapsedTimer){clearInterval(elapsedTimer);elapsedTimer=null;}
  btn.disabled=false;
  btn.textContent='开始检测';
}
function renderHitEventsHtml(events){
  if(!events||!events.length)return '';
  var cards='',desk='<div class="table-wrap detect-hit-desk"><table><thead><tr><th>时间</th><th>方向</th><th>振幅</th></tr></thead><tbody>';
  events.forEach(function(ev,i){
    cards+='<div class="hit-event-card">';
    cards+='<div class="hev-idx">#'+(i+1)+'</div>';
    cards+='<div class="hev-row"><span class="hev-label">时间</span><span class="hev-val">'+escHtml(ev.timestamp)+'</span></div>';
    cards+='<div class="hev-row"><span class="hev-label">方向</span><span class="hev-val">'+escHtml(ev.direction)+'</span></div>';
    cards+='<div class="hev-row"><span class="hev-label">振幅</span><span class="hev-val amp">'+escHtml(ev.amplitude)+'%</span></div>';
    cards+='</div>';
    desk+='<tr><td>'+escHtml(ev.timestamp)+'</td><td>'+escHtml(ev.direction)+'</td><td class="amp">'+escHtml(ev.amplitude)+'%</td></tr>';
  });
  desk+='</tbody></table></div>';
  return '<h4 class="hit-events-title">命中详情（'+events.length+' 条）</h4><div class="hit-event-cards">'+cards+'</div>'+desk;
}
function renderDetectResult(d){
  var h='<div class="result-card"><h3>'+d.symbol+' 报告</h3>';
  h+='<p class="hint">交易所：'+(d.exchange||'-')+' · 数据源：'+(d.data_source||'-')+' · 周期：'+(d.timeframe||'-')+' · 回溯：'+(d.days||'-')+'天';
  if(d.data_start&&d.data_end){h+=' · 数据范围：'+d.data_start+' ~ '+d.data_end;}
  if(d.expected_klines){h+=' · 预期K线：'+d.expected_klines+' · 覆盖率：'+(d.coverage_ratio||0)+'%';}
  h+='</p>';
  if(d.data_warning){h+='<div class="error-box" style="margin:10px 0;padding:10px"><strong>数据提醒</strong><br>'+d.data_warning+'</div>';}
  if(d.points_result){
    var pr=d.points_result;
    h+='<div class="'+(pr.awarded?'ok-box':'hint-box')+'" style="margin:10px 0;padding:10px;border:1px solid '+(pr.awarded?'#3ecf8e':'#1e3a5f')+';border-radius:8px;color:'+(pr.awarded?'#3ecf8e':'#8892b0')+'">积分：'+pr.message+(pr.total_points!=null?' · 当前 '+pr.total_points:'')+'</div>';
  }
  h+='<div class="result-stats">';
  h+='<div class="stat"><span class="n">'+d.total_klines.toLocaleString()+'</span><div class="lb">K线</div></div>';
  h+='<div class="stat"><span class="n">'+d.hit_count+'</span><div class="lb">命中</div></div>';
  h+='<div class="stat"><span class="n">0</span><div class="lb">误报</div></div></div>';
  if(d.hit_count>0&&d.top_events&&d.top_events.length){h+=renderHitEventsHtml(d.top_events);}
  else{h+='<p style="color:var(--accent);margin-top:8px">未发现命中</p>';}
  h+='</div>';res.innerHTML=h;
}
btn.disabled=true;btn.textContent='检测中…';
res.innerHTML='<div class="loading"><div class="spinner"></div><p>正在抓取 '+sv+' 数据...</p><div class="progress-wrap"><div class="progress-track"><div class="progress-indet"></div></div></div><p class="elapsed-big" id="elapsedBig">0 秒</p><p class="hint" id="elapsed">已等待 0 秒 · 当前配置预计 10-30 秒完成</p></div>';
elapsedTimer=setInterval(function(){sec++;var el=document.getElementById('elapsed'),big=document.getElementById('elapsedBig');if(big)big.textContent=sec+' 秒';if(el){var msg='已等待 '+sec+' 秒';if(sec<30)msg+=' · 当前配置预计 10-30 秒完成';else if(sec<90)msg+=' · 交易所响应较慢，仍在处理中';else msg+=' · 已等待较久，可能是交易所网络慢或任务卡住';el.textContent=msg;}},1000);
var p=new URLSearchParams({exchange:ex.value,symbol:sv,timeframe:document.getElementById('tf').value,days:document.getElementById('d').value,amp:document.getElementById('a').value});
try{
// 提交任务
var r=await fetch('/api/detect?'+p.toString());
if(!r.ok){
  var errData=await r.json().catch(function(){return {message:'服务器响应错误 '+r.status};});
  res.innerHTML='<div class="error-box"><h3>错误</h3><p>'+(errData.message||'请求失败')+'</p></div>';
  return;
}
var taskData=await r.json();
if(taskData.status==='success'&&!taskData.task_id){
  finishDetection();
  renderDetectResult(taskData);
  return;
}
if(taskData.status!=='success'||!taskData.task_id){
  res.innerHTML='<div class="error-box"><h3>错误</h3><p>'+(taskData.message||'检测失败')+'</p></div>';
  return;
}

// 轮询任务状态
var taskId=taskData.task_id;
taskSubmitted=true;
var pollCount=0;
var pollErrors=0;
async function pollStatus(){
  pollCount++;
  if(pollCount>180){finishDetection();res.innerHTML='<div class="error-box"><h3>超时</h3><p>任务执行时间过长（超过3分钟），请减少回溯天数或换个交易对</p></div>';return true;}
  try{
    var statusRes=await fetch('/api/detect/status/'+taskId);
    if(!statusRes.ok){
      finishDetection();
      var errJson=await statusRes.json().catch(function(){return null;});
      var errText=errJson&&errJson.message?errJson.message:await statusRes.text().catch(function(){return '任务查询失败';});
      res.innerHTML='<div class="error-box"><h3>检测失败</h3><p>'+errText+'</p></div>';
      return true;
    }
    var statusData=await statusRes.json();
    pollErrors=0;
    if(statusData.elapsed_seconds&&statusData.status==='pending'){
      var el=document.getElementById('elapsed');
      if(el)el.textContent='已等待 '+Math.round(statusData.elapsed_seconds)+' 秒 · 服务端正在处理（'+(statusData.task_status||'running')+'）';
    }
    if(statusData.status==='success'){
      finishDetection();
      renderDetectResult(statusData);
      return true;
    }else if(statusData.status==='error'){
      finishDetection();
      res.innerHTML='<div class="error-box"><h3>检测失败</h3><p>'+statusData.message+'</p></div>';
      return true;
    }
    return false;
  }catch(pollErr){
    pollErrors++;
    console.warn('轮询暂时失败，第'+pollErrors+'次:',pollErr);
    var el=document.getElementById('elapsed');
    if(el)el.textContent='已等待 '+sec+' 秒 · 状态查询暂时失败，正在自动重试 '+pollErrors+'/10';
    if(pollErrors<10)return false;
    finishDetection();
    res.innerHTML='<div class="error-box"><h3>检测失败</h3><p>状态查询连续失败，请刷新页面后重试。错误: '+pollErr.message+'</p></div>';
    return true;
  }
}
if(!(await pollStatus())){
  var pollInterval=setInterval(async function(){
    var done=await pollStatus();
    if(done)clearInterval(pollInterval);
  },1000);
}

}catch(err){
  var errMsg='网络请求失败';
  var hint='建议：减少回溯天数、检查网络连接、或稍后重试。';
  if(err.name==='TimeoutError'){
    errMsg='请求超时（>10分钟），请减少回溯天数或换个交易对';
  }else if(err.name==='TypeError'&&err.message.includes('fetch')){
    errMsg='无法连接到服务器';
    hint='可能原因：1) 服务器未启动 2) 网络连接问题 3) 服务器负载过高。请联系管理员或稍后重试。';
  }else if(err.message){
    errMsg=err.message;
  }
  console.error('检测失败详情:',err);
  res.innerHTML='<div class="error-box"><h3>失败</h3><p>'+errMsg+'</p><p class="hint">'+hint+'</p></div>';
}
finally{if(!taskSubmitted)finishDetection();}});

fillDatePick();
loadDaily();
startPoll();
})();
</script>
</body>
</html>
'''


@app.route("/")
def landing():
    """官网首页"""
    return render_template("landing.html")




@app.route("/detect")
def index():
    session["is_admin"] = False
    return render_template_string(HTML, is_admin=False)


@app.route(ADMIN_PATH)
def admin_index():
    session["is_admin"] = True
    return render_template_string(HTML, is_admin=True)


@app.route("/api/health")
def api_health():
    """轻量健康检查（监控/部署探活）"""
    return jsonify({
        "status": "ok",
        "ts": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    })


@app.route("/api/symbols")
def api_symbols():
    eid = request.args.get("exchange", "binanceusdm")
    now = time.time()
    cached = _SYMBOLS_CACHE.get(eid)
    if cached and now - cached["ts"] < _SYMBOLS_CACHE_TTL:
        return jsonify(cached["data"])
    try:
        import ccxt

        ex = getattr(ccxt, eid)({"enableRateLimit": True, "timeout": 30000, "options": {"defaultType": "swap"}})
        ex.load_markets(reload=True)
        syms = []
        
        for s, m in ex.markets.items():
            # 放宽条件：只要是衍生品市场就可以
            market_type = m.get("type", "")
            is_derivative = (
                m.get("swap") or 
                m.get("linear") or 
                market_type in ("swap", "future", "delivery") or
                m.get("future") or
                m.get("contract")
            )
            if not is_derivative:
                continue
            
            # 支持 USDT、USD、USDC 计价
            quote = m.get("quote")
            if quote not in ("USDT", "USD", "USDC"):
                continue
            
            clean = s.split(":")[0] if ":" in s else s
            # 接受 /USDT、/USD、/USDC 结尾
            if clean.endswith("/USDT") or clean.endswith("/USD") or clean.endswith("/USDC"):
                syms.append(clean)
        
        if not syms:
            return jsonify(
                {
                    "status": "error",
                    "message": f"{eid} 没有找到支持的永续合约（USDT/USD/USDC）",
                    "symbols": [],
                    "count": 0,
                }
            )
        
        payload = {
            "status": "success",
            "symbols": sorted(list(set(syms))),
            "count": len(syms),
        }
        _SYMBOLS_CACHE[eid] = {"ts": now, "data": payload}
        return jsonify(payload)
    except Exception as e:
        return jsonify(
            {
                "status": "error",
                "message": f"{type(e).__name__}: {str(e)[:150]}",
                "symbols": [],
                "count": 0,
            }
        )


@app.route("/api/detect")
def api_detect():
    """自选合约检测：同步执行并直接返回结果，优先保证核心功能稳定"""
    ex = request.args.get("exchange", "binanceusdm")
    sym = request.args.get("symbol", "SOL/USDT").strip()
    tf = request.args.get("timeframe", "15m")
    d = request.args.get("days", "1")
    a = request.args.get("amp", "3.0")
    
    if not sym or "/" not in sym:
        return jsonify({"status": "error", "message": "交易对格式错误"}), 400

    try:
        from wick_detector_v4 import LiquidationDetector, get_default_amplitude, fast_fetch_ohlcv_rest, _timeframe_to_ms

        try:
            days_int = max(1, min(int(d), 30))
        except Exception:
            days_int = 1
        try:
            amp_float = float(a) if a not in (None, "") else get_default_amplitude(sym)
        except Exception:
            amp_float = get_default_amplitude(sym)

        timeframe_ms = _timeframe_to_ms(tf)
        expected_klines = int(days_int * 24 * 60 * 60 * 1000 / timeframe_ms) if timeframe_ms > 0 else 0

        df = fast_fetch_ohlcv_rest(ex, sym, timeframe=tf, days_back=days_int)
        data_source = 'rest'
        rest_count = int(len(df)) if df is not None else 0
        rest_coverage = (rest_count / expected_klines) if expected_klines else 1
        if df is None or df.empty or rest_coverage < 0.9:
            detector = LiquidationDetector(exchange_name=ex, symbol=sym)
            ccxt_df = detector.fetch_ohlcv_live(timeframe=tf, days_back=days_int, market_type='swap')
            if ccxt_df is not None and not ccxt_df.empty and len(ccxt_df) > rest_count:
                df = ccxt_df
                data_source = 'ccxt_fallback'
            elif rest_count > 0:
                data_source = 'rest_partial'
        if df is None or df.empty:
            return jsonify({
                "status": "error",
                "message": "未能获取K线数据：交易所接口无数据、交易对不支持，或服务器到交易所网络异常"
            }), 500

        detector_for_scan = LiquidationDetector(exchange_name=None, symbol=sym)
        df_scored = detector_for_scan.detect_wicks(
            df,
            min_amplitude_pct=amp_float,
            body_ratio_threshold=0.5,
            wick_ratio_threshold=5.0,
            rebound_threshold=0.7,
        )
        hits = df_scored[df_scored['wick_score'] >= 0.8].copy() if 'wick_score' in df_scored.columns else df_scored.iloc[0:0]

        top_events = []
        if not hits.empty:
            sorted_hits = hits.sort_values('amplitude', ascending=False)
            for _, row in sorted_hits.iterrows():
                top_events.append({
                    "timestamp": str(row.get('timestamp', '')),
                    "direction": str(row.get('direction', '')),
                    "amplitude": f"{float(row.get('amplitude', 0)):.2f}",
                })

        coverage_ratio = round((len(df_scored) / expected_klines) * 100, 1) if expected_klines else 0
        data_warning = ""
        if expected_klines and len(df_scored) < expected_klines * 0.9:
            data_warning = f"数据覆盖不足：预期约{expected_klines}根，实际{len(df_scored)}根，请谨慎参考"

        points_result = None
        wallet_address = session.get("wallet_address")
        if wallet_address:
            awarded, points_msg, points_delta = points_system.add_points(
                wallet_address,
                "detection",
                metadata={"exchange": ex, "symbol": sym, "timeframe": tf, "days": days_int, "hit_count": int(len(hits))}
            )
            user_after = points_system.get_user(wallet_address)
            points_result = {
                "awarded": bool(awarded),
                "message": points_msg,
                "points": points_delta,
                "total_points": user_after["total_points"] if user_after else None,
            }

        return jsonify({
            "status": "success",
            "exchange": ex,
            "symbol": sym,
            "timeframe": tf,
            "days": days_int,
            "data_start": str(df_scored['timestamp'].iloc[0]) if 'timestamp' in df_scored.columns and len(df_scored) > 0 else "",
            "data_end": str(df_scored['timestamp'].iloc[-1]) if 'timestamp' in df_scored.columns and len(df_scored) > 0 else "",
            "data_source": data_source,
            "expected_klines": expected_klines,
            "coverage_ratio": coverage_ratio,
            "data_warning": data_warning,
            "points_result": points_result,
            "total_klines": int(len(df_scored)),
            "hit_count": int(len(hits)),
            "top_events": top_events
        })
    except Exception as e:
        msg = str(e) or type(e).__name__
        lower_msg = msg.lower()
        if any(x in lower_msg for x in ['403', '451', 'forbidden', 'restricted location', 'cloudfront', 'access denied']):
            msg = '交易所接口拒绝访问，服务器 IP 可能被地区限制或风控拦截。原始错误：' + msg[-500:]
        elif any(x in lower_msg for x in ['ratelimit', 'rate limit', 'ddosprotection', 'too many requests', '429']):
            msg = '交易所接口触发限流，请稍后重试或减少频率。原始错误：' + msg[-500:]
        elif any(x in lower_msg for x in ['requesttimeout', 'timeout', 'timed out']):
            msg = '交易所接口请求超时，可能是服务器网络到该交易所不稳定或被限速。原始错误：' + msg[-500:]
        return jsonify({"status": "error", "message": msg[-1000:]}), 500


@app.route("/api/detect/status/<task_id>")
def api_detect_status(task_id):
    """查询检测任务状态"""
    task = load_task(task_id)
    if not task:
        return jsonify({"status": "error", "message": "任务不存在"}), 404
    
    if task['status'] == 'completed':
        return jsonify(task['result'])
    elif task['status'] == 'error':
        return jsonify({
            "status": "error",
            "message": task.get('error', '未知错误')
        }), 500
    else:
        # pending 或 running
        elapsed_seconds = max(0, int(time.time() - task.get('created_at', time.time())))
        if elapsed_seconds > 180:
            return jsonify({
                "status": "error",
                "message": "任务已运行超过3分钟，可能卡在交易所接口或服务器网络，请稍后重试"
            }), 500
        return jsonify({
            "status": "pending",
            "message": "任务处理中...",
            "task_status": task['status'],
            "elapsed_seconds": elapsed_seconds
        })


@app.route("/api/detect_sync")
def api_detect_sync():
    ex = request.args.get("exchange", "binanceusdm")
    sym = request.args.get("symbol", "SOL/USDT").strip()
    tf = request.args.get("timeframe", "5m")
    d = request.args.get("days", "3")
    a = request.args.get("amp", "3.0")
    if not sym or "/" not in sym:
        return jsonify({"status": "error", "message": "交易对格式错误"}), 400
    base = os.path.dirname(os.path.abspath(__file__))
    sp = os.path.join(base, "wick_detector_v4.py")
    if not os.path.exists(sp):
        return jsonify({"status": "error", "message": "检测脚本未找到"}), 500
    py = os.environ.get("PYTHON", "python3")
    try:
        r = subprocess.run(
            [
                py,
                sp,
                "--exchange",
                ex,
                "--mode",
                "live",
                "--symbol",
                sym,
                "--timeframe",
                tf,
                "--days",
                str(d),
                "--amp",
                str(a),
                "--no_debug",
            ],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=base,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        
        # 检查返回码
        if r.returncode != 0:
            err_msg = r.stderr.strip() or r.stdout.strip() or "脚本执行失败"
            lower_err = err_msg.lower()
            if any(x in lower_err for x in ['403', '451', 'forbidden', 'restricted location', 'cloudfront', 'access denied']):
                err_msg = "交易所接口拒绝访问，服务器 IP 可能被地区限制或风控拦截。原始错误：" + err_msg[-500:]
            elif any(x in lower_err for x in ['ratelimit', 'rate limit', 'ddosprotection', 'too many requests', '429']):
                err_msg = "交易所接口触发限流，请稍后重试或减少频率。原始错误：" + err_msg[-500:]
            elif any(x in lower_err for x in ['requesttimeout', 'timeout', 'timed out']):
                err_msg = "交易所接口请求超时，可能是服务器网络到该交易所不稳定或被限速。原始错误：" + err_msg[-500:]
            return jsonify(
                {
                    "status": "error",
                    "message": f"执行失败: {err_msg[:800]}",
                    "returncode": r.returncode,
                }
            ), 500
        
        o = r.stdout.strip()
        if not o:
            return jsonify(
                {
                    "status": "error",
                    "message": "脚本无输出，可能是网络问题或交易所限流",
                    "stderr": r.stderr[:300] if r.stderr else "",
                }
            ), 500
        
        tk, hc, te = 0, 0, []
        km = re.search(r"总K线数:\s+([\d,]+)", o)
        hm = re.search(r"命中事件:\s+(\d+)", o)
        if km:
            tk = int(km.group(1).replace(",", ""))
        if hm:
            hc = int(hm.group(1))
        ts = o.split("振幅最大 Top")
        if len(ts) > 1:
            for line in ts[1].strip().split("\n"):
                line = line.strip()
                if not line or "|" not in line:
                    continue
                if ". " in line[:6]:
                    line = line.split(". ", 1)[1]
                ps = [p.strip() for p in line.split("|")]
                if len(ps) >= 3:
                    try:
                        te.append(
                            {
                                "timestamp": ps[0],
                                "direction": ps[1],
                                "amplitude": ps[2]
                                .replace("振幅:", "")
                                .replace("%", "")
                                .strip(),
                            }
                        )
                    except Exception:
                        pass
        return jsonify(
            {
                "status": "success",
                "exchange": ex,
                "symbol": sym,
                "total_klines": tk,
                "hit_count": hc,
                "top_events": te,
            }
        )
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "超时"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/daily/latest")
def api_daily_latest():
    try:
        daily_scan._ensure_db()
        reports = daily_scan.get_all_latest_reports()
        st = daily_scan.get_job_state()
        if not any(v is not None for v in reports.values()):
            return jsonify({"status": "empty", "reports": reports, "job": st})
        return jsonify({"status": "ok", "reports": reports, "job": st})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/daily/snapshot")
def api_daily_snapshot():
    """指定数据日 YYYY-MM-DD，返回三所该日的存档（若有）。"""
    d = request.args.get("date")
    if not d:
        return jsonify({"status": "error", "message": "缺少 date"}), 400
    try:
        daily_scan._ensure_db()
        reports = {}
        for ex in daily_scan._daily_exchange_ids():
            reports[ex] = daily_scan.get_report_by_date(d, exchange_id=ex)
        st = daily_scan.get_job_state()
        if not any(v is not None for v in reports.values()):
            return jsonify(
                {"status": "empty", "date": d, "reports": reports, "job": st}
            )
        return jsonify({"status": "ok", "date": d, "reports": reports, "job": st})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/daily/report")
def api_daily_report():
    d = request.args.get("date")
    ex = request.args.get("exchange", "binanceusdm")
    if not d:
        return jsonify({"status": "error", "message": "缺少 date"}), 400
    try:
        daily_scan._ensure_db()
        rep = daily_scan.get_report_by_date(d, exchange_id=ex)
        if not rep:
            return jsonify({"status": "empty"})
        return jsonify({"status": "ok", "report": rep})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/daily/dates")
def api_daily_dates():
    try:
        daily_scan._ensure_db()
        return jsonify({"status": "ok", "dates": daily_scan.list_report_dates()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/daily/status")
def api_daily_status():
    return jsonify(daily_scan.get_job_state())


@app.route("/api/daily/trigger", methods=["POST"])
def api_daily_trigger():
    if not session.get("is_admin"):
        return jsonify({"started": False, "message": "仅管理员页面可执行后台生成"}), 403
    ok = daily_scan.start_daily_job_async()
    if ok:
        return jsonify({"started": True})
    return jsonify({"started": False, "message": "已有任务在运行"})


def _start_scheduler():
    if os.environ.get("DISABLE_DAILY_SCHEDULER") == "1":
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        tz = ZoneInfo(os.environ.get("DAILY_REPORT_TZ", "Asia/Shanghai"))
        h = int(os.environ.get("DAILY_CRON_HOUR", "8"))
        m = int(os.environ.get("DAILY_CRON_MINUTE", "0"))
        sched = BackgroundScheduler(timezone=tz)
        sched.add_job(
            lambda: daily_scan.start_daily_job_async(),
            CronTrigger(hour=h, minute=m, timezone=tz),
            id="morning_wick_daily",
            replace_existing=True,
        )
        sched.start()
        print(
            f"[scheduler] 每日日报已调度: {tz.key} {h:02d}:{m:02d} (DISABLE_DAILY_SCHEDULER=1 可关闭)"
        )
    except Exception as e:
        print("[scheduler] APScheduler 未启动:", e)


# ==================== 积分系统 API ====================

@app.route("/api/points/nonce", methods=["POST"])
def api_points_nonce():
    """生成登录 nonce"""
    data = request.get_json() or {}
    wallet_address = data.get("wallet_address", "").strip()
    
    if not wallet_address:
        return jsonify({"error": "缺少钱包地址"}), 400
    if not points_system.is_valid_wallet_address(wallet_address):
        return jsonify({"error": "钱包地址格式无效"}), 400

    nonce = points_system.generate_nonce(wallet_address)
    return jsonify({"nonce": nonce, "message": f"请使用钱包签名此消息: {nonce}"})


@app.route("/api/points/login", methods=["POST"])
def api_points_login():
    """Web3 钱包登录"""
    data = request.get_json() or {}
    wallet_address = data.get("wallet_address", "").strip()
    signature = data.get("signature", "").strip()
    nonce = data.get("nonce", "").strip()
    
    if not all([wallet_address, signature, nonce]):
        return jsonify({"error": "缺少必要参数"}), 400
    if not points_system.is_valid_wallet_address(wallet_address):
        return jsonify({"error": "钱包地址格式无效"}), 400

    if not points_system.verify_nonce(wallet_address, nonce):
        return jsonify({"error": "Nonce 无效或已过期"}), 401

    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        recovered = Account.recover_message(encode_defunct(text=nonce), signature=signature)
        if recovered.lower() != wallet_address.lower():
            return jsonify({"error": "钱包签名验证失败"}), 401
    except ImportError:
        return jsonify({"error": "服务端缺少 eth-account，无法验证钱包签名"}), 500
    except Exception:
        return jsonify({"error": "钱包签名无效"}), 401
    
    invited_by = (data.get("invited_by") or data.get("invite") or "").strip()
    user = points_system.create_or_get_user(wallet_address, invited_by)
    
    session["wallet_address"] = wallet_address
    session["logged_in"] = True
    
    return jsonify({
        "success": True,
        "user": {
            "wallet_address": user["wallet_address"],
            "total_points": user["total_points"],
            "level": user["level"],
            "level_info": points_system.get_level_info(user["total_points"]),
            "credit_score": user["credit_score"],
            "invite_code": user["invite_code"]
        }
    })


@app.route("/api/points/user", methods=["GET"])
def api_points_user():
    """获取当前用户信息"""
    wallet_address = session.get("wallet_address")
    if not wallet_address:
        return jsonify({"error": "未登录"}), 401
    
    user = points_system.get_user(wallet_address)
    if not user:
        return jsonify({"error": "用户不存在"}), 404
    
    badges = points_system.get_user_badges(wallet_address, limit=50)
    return jsonify({
        "wallet_address": user["wallet_address"],
        "total_points": user["total_points"],
        "level": user["level"],
        "level_info": user["level_info"],
        "credit_score": user["credit_score"],
        "invite_code": user["invite_code"],
        "is_test_user": points_system.is_test_user(wallet_address),
        "created_at": user["created_at"],
        "badges": badges,
        "weekly_champion_count": sum(
            1 for b in badges if b.get("badge_type") == points_system.BADGE_WEEKLY_CHAMPION
        ),
    })


@app.route("/api/points/checkin", methods=["POST"])
def api_points_checkin():
    """每日签到"""
    wallet_address = session.get("wallet_address")
    if not wallet_address:
        return jsonify({"error": "未登录"}), 401
    
    success, message, points = points_system.add_points(wallet_address, "daily_checkin")
    
    if success:
        user = points_system.get_user(wallet_address)
        return jsonify({
            "success": True,
            "message": message,
            "points": points,
            "total_points": user["total_points"]
        })
    else:
        return jsonify({"success": False, "message": message}), 400


@app.route("/api/points/history", methods=["GET"])
def api_points_history():
    """获取积分历史"""
    wallet_address = session.get("wallet_address")
    if not wallet_address:
        return jsonify({"error": "未登录"}), 401
    
    limit = request.args.get("limit", 50, type=int)
    limit = max(1, min(limit, points_system.LEADERBOARD_MAX_LIMIT))
    history = points_system.get_points_history(wallet_address, limit)
    
    return jsonify({"history": history})


@app.route("/api/points/leaderboard", methods=["GET"])
def api_points_leaderboard():
    """获取积分排行榜"""
    limit = request.args.get("limit", 100, type=int)
    limit = max(1, min(limit, points_system.LEADERBOARD_MAX_LIMIT))
    leaderboard = points_system.get_leaderboard(limit)
    
    return jsonify({
        "leaderboard": leaderboard,
        "weekly_rewards": points_system.get_weekly_rank_reward_rules(),
        "weekly_reward_top_n": points_system.WEEKLY_REWARD_TOP_N,
    })


@app.route("/api/points/weekly_rank_rewards", methods=["GET"])
def api_weekly_rank_rewards():
    """每周排行榜 USDT 奖励规则（公开）"""
    return jsonify({
        "success": True,
        "top_n": points_system.WEEKLY_REWARD_TOP_N,
        "rules": points_system.get_weekly_rank_reward_rules(),
        "note": "每周由管理员创建排行榜快照后，按名次发放 USDT 至绑定提币地址",
    })


@app.route("/api/points/badges/champions", methods=["GET"])
def api_weekly_champion_badges():
    """近期周冠军徽章（公开）"""
    limit = request.args.get("limit", 10, type=int)
    limit = max(1, min(limit, 50))
    return jsonify({
        "success": True,
        "champions": points_system.get_recent_weekly_champions(limit),
    })


@app.route("/api/points/logout", methods=["POST"])
def api_points_logout():
    """登出"""
    session.clear()
    return jsonify({"success": True, "message": "已登出"})


@app.route("/api/points/test_login", methods=["POST"])
def api_points_test_login():
    """测试登录（无需钱包）- 使用固定地址"""
    import hashlib

    if os.environ.get("ENABLE_TEST_LOGIN", "1").lower() not in ("1", "true", "yes"):
        return jsonify({"error": "测试登录已关闭"}), 403

    # 检查是否已有测试地址
    if "wallet_address" in session and session.get("is_test_user"):
        # 已经有测试地址，直接返回
        user = points_system.get_user(session["wallet_address"])
        if user:
            return jsonify({
                "success": True,
                "user": {
                    "wallet_address": user["wallet_address"],
                    "total_points": user["total_points"],
                    "level": user["level"],
                    "level_info": points_system.get_level_info(user["total_points"]),
                    "credit_score": user["credit_score"],
                    "invite_code": user["invite_code"]
                }
            })
    
    # 生成基于浏览器指纹的固定测试地址
    # 使用 User-Agent + IP 生成唯一标识
    user_agent = request.headers.get('User-Agent', '')
    ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
    
    # 生成固定的测试地址
    fingerprint = f"{user_agent}:{ip_address}:test_user"
    address_hash = hashlib.sha256(fingerprint.encode()).hexdigest()[:40]
    test_wallet = '0x' + address_hash
    
    # 创建或获取用户
    user = points_system.create_or_get_user(test_wallet)
    points_system.set_user_metadata(test_wallet, {"is_test_user": True, "test_fingerprint": address_hash})
    
    session.permanent = True  # 设置为永久session
    session["wallet_address"] = test_wallet
    session["logged_in"] = True
    session["is_test_user"] = True  # 标记为测试用户
    
    return jsonify({
        "success": True,
        "user": {
            "wallet_address": user["wallet_address"],
            "total_points": user["total_points"],
            "level": user["level"],
            "level_info": points_system.get_level_info(user["total_points"]),
            "credit_score": user["credit_score"],
            "invite_code": user["invite_code"]
        },
        "message": "测试登录成功！您的测试地址已固定，下次登录将使用相同地址。"
    })


@app.route("/api/points/tasks", methods=["GET"])
def api_points_tasks():
    """获取每日任务列表及完成状态"""
    wallet_address = session.get("wallet_address")
    if not wallet_address:
        return jsonify({"error": "未登录"}), 401
    
    today = points_system._today()
    
    # 检查今日各任务完成情况
    import sqlite3
    db_path = points_system.DB_PATH
    
    with sqlite3.connect(db_path, timeout=30.0) as conn:
        # 检查每日签到
        checkin_done = conn.execute("""
            SELECT COUNT(*) FROM daily_actions
            WHERE wallet_address = ? AND action_type = 'daily_checkin' AND action_date = ?
        """, (wallet_address.lower(), today)).fetchone()[0] > 0
        
        # 检查自选检测
        detection_count = conn.execute("""
            SELECT COALESCE(count, 0) FROM daily_actions
            WHERE wallet_address = ? AND action_type = 'detection' AND action_date = ?
        """, (wallet_address.lower(), today)).fetchone()
        detection_count = detection_count[0] if detection_count else 0

        share_count = conn.execute("""
            SELECT COALESCE(count, 0) FROM daily_actions
            WHERE wallet_address = ? AND action_type = 'share_report' AND action_date = ?
        """, (wallet_address.lower(), today)).fetchone()
        share_count = share_count[0] if share_count else 0

        invite_total = conn.execute("""
            SELECT COALESCE(count, 0) FROM daily_actions
            WHERE wallet_address = ? AND action_type = 'invite_user' AND action_date = ?
        """, (wallet_address.lower(), today)).fetchone()
        invite_today = invite_total[0] if invite_total else 0

        case_count = conn.execute("""
            SELECT COALESCE(count, 0) FROM daily_actions
            WHERE wallet_address = ? AND action_type = 'submit_case' AND action_date = ?
        """, (wallet_address.lower(), today)).fetchone()
        case_today = case_count[0] if case_count else 0
    
    tasks = [
        {
            "id": "daily_checkin",
            "name": "每日签到",
            "description": "每天签到一次，轻松获得积分奖励",
            "points": 10,
            "completed": checkin_done,
            "progress": "1/1" if checkin_done else "0/1"
        },
        {
            "id": "detection",
            "name": "使用检测功能",
            "description": "使用自选合约检测功能，每日最多奖励3次",
            "points": 5,
            "completed": detection_count >= 3,
            "count": int(detection_count),
            "limit": 3,
            "progress": f"{min(detection_count, 3)}/3"
        },
        {
            "id": "share_report",
            "name": "分享日报到 X",
            "description": "分享每日爆仓日报到 X，每日最多奖励1次",
            "points": 30,
            "completed": share_count >= 1,
            "count": int(share_count),
            "limit": 1,
            "progress": f"{min(share_count, 1)}/1"
        },
        {
            "id": "invite_user",
            "name": "邀请新用户",
            "description": "邀请好友注册，双方各得100积分",
            "points": 100,
            "completed": False,
            "count": int(invite_today),
            "limit": 0,
            "progress": f"今日 {invite_today} 人"
        },
        {
            "id": "submit_case",
            "name": "提交爆仓案例",
            "description": "提交真实爆仓案例，审核通过额外奖励",
            "points": 50,
            "completed": case_today >= 2,
            "count": int(case_today),
            "limit": 2,
            "progress": f"{min(case_today, 2)}/2"
        }
    ]
    
    return jsonify({"tasks": tasks})


@app.route("/api/points/share_report", methods=["POST"])
def api_points_share_report():
    """分享日报：须提交已发布推文链接，校验格式与最短等待时间后发放积分。"""
    wallet_address = session.get("wallet_address")
    if not wallet_address:
        return jsonify({"error": "未登录"}), 401

    if points_system.is_test_user(wallet_address):
        return jsonify({"error": "测试账号不能领取分享奖励，请连接真实钱包"}), 403

    data = request.get_json(silent=True) or {}
    tweet_url = (data.get("tweet_url") or data.get("url") or "").strip()
    if not tweet_url:
        return jsonify({"success": False, "message": "请先发布推文，再粘贴推文链接领取积分"}), 400

    opened_at = data.get("share_opened_at")
    if opened_at is not None:
        try:
            opened_at = float(opened_at)
        except (TypeError, ValueError):
            opened_at = None

    success, message, points = points_system.claim_share_report(
        wallet_address,
        tweet_url,
        share_opened_at=opened_at,
    )
    user = points_system.get_user(wallet_address)
    return jsonify({
        "success": bool(success),
        "message": message,
        "points": points,
        "total_points": user["total_points"] if user else None,
        "min_delay_seconds": points_system.share_min_delay_seconds(),
    }), (200 if success else 400)


@app.route("/points")


def points_page():
    """积分系统页面"""
    return render_template("points.html")


def init_app():
    daily_scan._ensure_db()
    points_system._ensure_db()
    # 初始化默认兑换规则（可选，首次运行时执行）
    try:
        points_system.init_default_exchange_rule()
    except Exception as e:
        print(f"⚠️  兑换规则初始化失败（可忽略）: {e}")
    _start_scheduler()


init_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)



@app.route("/leaderboard")
def leaderboard_page():
    """积分排行榜页面"""
    return render_template("leaderboard.html")

@app.route("/points/history")
def points_history_page():
    """积分历史页面"""
    return render_template("points_history.html")

@app.route("/invite")
def invite_page():
    """邀请好友页面"""
    return render_template("invite.html")

@app.route("/tasks")
def daily_tasks_page():
    """每日任务页面"""
    return render_template("daily_tasks.html")

@app.route("/shop")
def points_shop_page():
    """积分商城页面"""
    return render_template("points_shop.html")

# 暂未开放链上/随机类兑换（前端已 disabled，后端须一致拦截）
_SPEND_DISABLED_ITEMS = frozenset({"nft_badge", "lottery"})


@app.route("/api/points/spend", methods=["POST"])
def api_points_spend():
    """消耗积分兑换商品"""
    if "wallet_address" not in session:
        return jsonify({"error": "未登录"}), 401
    
    data = request.get_json(silent=True) or {}
    item_type = (data.get("item_type") or "").strip()
    if not item_type:
        return jsonify({"success": False, "message": "缺少商品类型"}), 400

    if item_type in _SPEND_DISABLED_ITEMS:
        return jsonify({"success": False, "message": "该商品暂未开放，敬请期待"}), 400

    expected_cost = points_system.POINTS_COST.get(item_type)
    if expected_cost is None:
        return jsonify({"success": False, "message": f"未知商品: {item_type}"}), 400

    client_cost = data.get("cost")
    if client_cost is not None:
        try:
            if int(client_cost) != int(expected_cost):
                return jsonify({
                    "success": False,
                    "message": "商品价格不匹配，请刷新页面后重试",
                }), 400
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "无效的价格参数"}), 400
    cost = int(expected_cost)

    wallet_address = session["wallet_address"]
    if points_system.is_test_user(wallet_address):
        return jsonify({"success": False, "message": "测试账号仅用于体验，不能兑换奖励。请连接真实钱包后再兑换。"}), 403
    
    # 检查用户积分
    user = points_system.get_user(wallet_address)
    if not user:
        return jsonify({"error": "用户不存在"}), 404
    
    if user["total_points"] < cost:
        return jsonify({"success": False, "message": "积分不足"}), 200
    
    # 扣除积分
    success, message, _ = points_system.add_points(
        wallet_address, 
        item_type, 
        points=-cost,
        metadata={"action": "purchase", "item": item_type, "cost": cost}
    )
    
    if success:
        return jsonify({
            "success": True,
            "message": f"兑换成功！剩余积分：{user['total_points'] - cost}",
            "remaining_points": user["total_points"] - cost
        })
    else:
        return jsonify({"success": False, "message": message})


@app.route("/api/points/exchange_rules", methods=["GET"])
def api_get_exchange_rules():
    """获取当前兑换规则"""
    rule = points_system.get_current_exchange_rule()
    if not rule:
        return jsonify({"error": "暂无兑换规则"}), 404
    
    return jsonify({
        "success": True,
        "rule": rule
    })


@app.route("/api/points/exchange_rules/history", methods=["GET"])
def api_get_exchange_rules_history():
    """获取兑换规则历史"""
    limit = request.args.get("limit", 10, type=int)
    history = points_system.get_exchange_rules_history(limit)
    
    return jsonify({
        "success": True,
        "history": history
    })


@app.route("/api/points/exchange_rules", methods=["POST"])
def api_create_exchange_rule():
    """创建新的兑换规则（管理员）"""
    if not is_points_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    
    data = request.get_json()
    
    try:
        rule = points_system.create_exchange_rule(
            exchange_rate=float(data['exchange_rate']),
            min_amount=int(data['min_amount']),
            max_amount=int(data['max_amount']),
            daily_limit=int(data['daily_limit']),
            weekly_limit=int(data['weekly_limit']),
            settlement_days=int(data['settlement_days']),
            require_kyc=bool(data.get('require_kyc', False)),
            created_by=session.get("wallet_address", "admin"),
            notes=data.get('notes', '')
        )
        admin_audit("exchange_rule_create", target=str(rule.get("id")), detail=data)
        
        return jsonify({
            "success": True,
            "rule": rule,
            "message": "兑换规则已更新"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/points/invite_stats", methods=["GET"])
def api_invite_stats():
    """获取邀请统计"""
    if "wallet_address" not in session:
        return jsonify({"error": "未登录"}), 401
    
    wallet_address = session["wallet_address"]
    stats = points_system.get_invite_stats(wallet_address)
    
    return jsonify(stats)

@app.route("/leaderboard/rewards")
def leaderboard_rewards_page():
    """排行榜奖励页面"""
    return render_template("leaderboard_rewards.html")


@app.route("/admin/points")
def points_admin_hub_page():
    """积分管理后台入口"""
    if not is_points_admin():
        return "需要管理员权限", 403
    return render_template("points_admin.html")


@app.route("/admin/exchange_rules")
def exchange_rules_admin_page():
    """兑换规则管理页面（管理员）"""
    if not is_points_admin():
        return "需要管理员权限", 403
    return render_template("exchange_rules_admin.html")


@app.route("/admin/rewards")
def rewards_admin_page():
    """奖励发放管理页面（管理员）"""
    if not is_points_admin():
        return "需要管理员权限", 403
    return render_template("rewards_admin.html")


# ==================== 周排名快照 API ====================

@app.route("/api/points/snapshots", methods=["GET"])
def api_get_snapshots():
    """获取历史快照"""
    limit = request.args.get("limit", 10, type=int)
    snapshots = points_system.get_weekly_snapshots(limit)
    
    return jsonify({
        "success": True,
        "snapshots": snapshots
    })


@app.route("/api/points/snapshots/<int:snapshot_id>", methods=["GET"])
def api_get_snapshot_detail(snapshot_id):
    """获取周排名快照详情（管理员）"""
    if not is_points_admin():
        return jsonify({"error": "需要管理员权限"}), 403

    detail = points_system.get_weekly_snapshot_detail(snapshot_id)
    if not detail:
        return jsonify({"error": "快照不存在"}), 404

    return jsonify({"success": True, "snapshot": detail})


@app.route("/api/points/snapshots", methods=["POST"])
def api_create_snapshot():
    """创建周排名快照（管理员）"""
    if not is_points_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    
    data = request.get_json()
    
    try:
        snapshot = points_system.create_weekly_snapshot(
            week_start=data['week_start'],
            week_end=data['week_end'],
            created_by=session.get("wallet_address", "admin")
        )
        admin_audit("weekly_snapshot_create", target=str(snapshot.get("id")), detail=data)
        
        return jsonify({
            "success": True,
            "snapshot": snapshot
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== 奖励发放 API ====================

@app.route("/api/points/rewards/pending", methods=["GET"])
def api_get_pending_rewards():
    """获取待发放的奖励（管理员）"""
    if not is_points_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    
    snapshot_id = request.args.get("snapshot_id", type=int)
    rewards = points_system.get_pending_rewards(snapshot_id)
    
    return jsonify({
        "success": True,
        "rewards": rewards
    })


@app.route("/api/points/rewards/<int:reward_id>/approve", methods=["POST"])
def api_approve_reward(reward_id):
    """审核通过奖励（管理员）"""
    if not is_points_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    
    try:
        reward = points_system.approve_reward(
            reward_id=reward_id,
            approved_by=session.get("wallet_address", "admin")
        )
        admin_audit("reward_approve", target=str(reward_id))
        
        return jsonify({
            "success": True,
            "reward": reward
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/points/rewards/<int:reward_id>/txhash", methods=["POST"])
def api_record_txhash(reward_id):
    """记录奖励的链上交易哈希（管理员）"""
    if not is_points_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    
    data = request.get_json()
    txhash = data.get("txhash")
    
    if not txhash:
        return jsonify({"error": "缺少 txhash"}), 400
    
    try:
        reward = points_system.record_reward_txhash(
            reward_id=reward_id,
            txhash=txhash,
            distributed_by=session.get("wallet_address", "admin")
        )
        admin_audit("reward_distribute", target=str(reward_id), detail={"txhash": txhash})
        
        return jsonify({
            "success": True,
            "reward": reward
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/points/rewards/my", methods=["GET"])
def api_get_my_rewards():
    """获取我的奖励记录"""
    if "wallet_address" not in session:
        return jsonify({"error": "未登录"}), 401
    
    wallet_address = session["wallet_address"]
    rewards = points_system.get_user_rewards(wallet_address)
    
    return jsonify({
        "success": True,
        "rewards": rewards
    })


@app.route("/api/points/rewards/all", methods=["GET"])
def api_get_all_rewards():
    """获取所有已发放的奖励记录（公开）"""
    limit = request.args.get("limit", 50, type=int)
    rewards = points_system.get_all_distributed_rewards(limit)
    
    return jsonify({
        "success": True,
        "rewards": rewards
    })


# ==================== 钱包地址管理 API ====================

@app.route("/api/points/withdrawal_address", methods=["GET"])
def api_get_withdrawal_address():
    """获取提币地址及冷却状态"""
    if "wallet_address" not in session:
        return jsonify({"error": "未登录"}), 401
    
    wallet_address = session["wallet_address"]
    info = points_system.get_withdrawal_address_info(wallet_address)
    
    return jsonify({"success": True, **info})


@app.route("/api/points/withdrawal_address", methods=["POST"])
def api_bind_withdrawal_address():
    """绑定提币地址（含修改冷却期）"""
    if "wallet_address" not in session:
        return jsonify({"error": "未登录"}), 401
    
    data = request.get_json()
    withdrawal_address = data.get("withdrawal_address")
    
    if not withdrawal_address:
        return jsonify({"error": "缺少提币地址"}), 400
    
    wallet_address = session["wallet_address"]
    if points_system.is_test_user(wallet_address):
        return jsonify({"error": "测试账号不能绑定提币地址，请连接真实钱包后再绑定"}), 403
    success, message = points_system.bind_withdrawal_address(wallet_address, withdrawal_address)
    
    if success:
        return jsonify({"success": True, "message": message})
    return jsonify({"error": message}), 400


# ==================== 爆仓案例 API ====================

@app.route("/cases")
def liquidation_cases_page():
    return render_template("liquidation_cases.html")


@app.route("/admin/cases")
def cases_admin_page():
    if not is_points_admin():
        return "需要管理员权限", 403
    return render_template("cases_admin.html")


@app.route("/api/points/cases", methods=["POST"])
def api_submit_case():
    if "wallet_address" not in session:
        return jsonify({"error": "未登录"}), 401
    wallet_address = session["wallet_address"]
    if points_system.is_test_user(wallet_address):
        return jsonify({"error": "测试账号不能提交案例，请连接真实钱包"}), 403

    data = request.get_json() or {}
    ok, message, case = points_system.submit_liquidation_case(wallet_address, data)
    if ok:
        return jsonify({"success": True, "message": message, "case": case})
    return jsonify({"success": False, "message": message}), 400


@app.route("/api/points/cases/mine", methods=["GET"])
def api_my_cases():
    if "wallet_address" not in session:
        return jsonify({"error": "未登录"}), 401
    cases = points_system.get_user_liquidation_cases(session["wallet_address"])
    return jsonify({"success": True, "cases": cases})


@app.route("/api/points/cases/pending", methods=["GET"])
def api_pending_cases():
    if not is_points_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    cases = points_system.get_pending_liquidation_cases()
    return jsonify({"success": True, "cases": cases})


@app.route("/api/points/cases/<int:case_id>/review", methods=["POST"])
def api_review_case(case_id):
    if not is_points_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    data = request.get_json() or {}
    try:
        case = points_system.review_liquidation_case(
            case_id=case_id,
            approve=bool(data.get("approve")),
            reviewer_wallet=session.get("wallet_address", "admin"),
            review_notes=data.get("review_notes", ""),
        )
        admin_audit(
            "case_review",
            target=str(case_id),
            detail={"approve": bool(data.get("approve")), "status": case.get("status")},
        )
        return jsonify({"success": True, "case": case})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ==================== 积分兑换人工审核 API ====================

@app.route("/admin/redemptions")
def redemptions_admin_page():
    if not is_points_admin():
        return "需要管理员权限", 403
    return render_template("redemptions_admin.html")


@app.route("/admin/audit")
def audit_admin_page():
    if not is_points_admin():
        return "需要管理员权限", 403
    return render_template("audit_admin.html")


@app.route("/api/points/redemptions", methods=["POST"])
def api_submit_redemption():
    if "wallet_address" not in session:
        return jsonify({"error": "未登录"}), 401
    wallet_address = session["wallet_address"]
    if points_system.is_test_user(wallet_address):
        return jsonify({"error": "测试账号不能申请兑换"}), 403

    data = request.get_json() or {}
    points_amount = int(data.get("points_amount", 0))
    ok, message, req = points_system.submit_exchange_redemption(wallet_address, points_amount)
    if ok:
        return jsonify({"success": True, "message": message, "request": req})
    return jsonify({"success": False, "message": message}), 400


@app.route("/api/points/redemptions/mine", methods=["GET"])
def api_my_redemptions():
    if "wallet_address" not in session:
        return jsonify({"error": "未登录"}), 401
    reqs = points_system.get_user_redemption_requests(session["wallet_address"])
    return jsonify({"success": True, "requests": reqs})


@app.route("/api/points/redemptions/pending", methods=["GET"])
def api_pending_redemptions():
    if not is_points_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    reqs = points_system.get_pending_redemption_requests()
    return jsonify({"success": True, "requests": reqs})


@app.route("/api/points/redemptions/<int:request_id>/review", methods=["POST"])
def api_review_redemption(request_id):
    if not is_points_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    data = request.get_json() or {}
    try:
        req = points_system.review_redemption_request(
            request_id=request_id,
            approve=bool(data.get("approve")),
            reviewer_wallet=session.get("wallet_address", "admin"),
            review_notes=data.get("review_notes", ""),
            txhash=data.get("txhash"),
        )
        admin_audit(
            "redemption_review",
            target=str(request_id),
            detail={"approve": bool(data.get("approve")), "status": req.get("status")},
        )
        return jsonify({"success": True, "request": req})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/points/redemptions/<int:request_id>/paid", methods=["POST"])
def api_mark_redemption_paid(request_id):
    if not is_points_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    data = request.get_json() or {}
    txhash = (data.get("txhash") or "").strip()
    if not txhash:
        return jsonify({"error": "缺少 txhash"}), 400
    try:
        req = points_system.mark_redemption_paid(
            request_id=request_id,
            txhash=txhash,
            operator_wallet=session.get("wallet_address", "admin"),
        )
        admin_audit("redemption_paid", target=str(request_id), detail={"txhash": txhash})
        return jsonify({"success": True, "request": req})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/admin/audit_logs", methods=["GET"])
def api_admin_audit_logs():
    if not is_points_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    limit = request.args.get("limit", 100, type=int)
    action = request.args.get("action")
    logs = points_system.get_admin_audit_logs(limit=limit, action=action)
    return jsonify({"success": True, "logs": logs, "rate_limit_backend": redis_rate_limit.backend_name()})
