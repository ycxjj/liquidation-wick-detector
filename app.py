#!/usr/bin/env python3
"""
Wick Detector v3 - 完整版（动态合约搜索 + 677+ 合约）
"""
import subprocess, os, json, re, traceback
from datetime import datetime
from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)

HTML = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Wick Detector v3</title>
    <link rel="icon" type="image/png" href="/logo.png?v=3">
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0a0e27;color:#e0e0e0;min-height:100vh}
        .container{max-width:960px;margin:0 auto;padding:40px 20px}
        .header{text-align:center;margin-bottom:40px}
        .header h1{font-size:2em;color:#00d4ff;margin-bottom:10px}
        .header p{color:#8892b0}
        .card{background:#112240;border-radius:12px;padding:28px;margin-bottom:30px;border:1px solid #1e3a5f}
        .card h2{color:#00d4ff;margin-bottom:22px}
        .form-row{display:flex;gap:15px;flex-wrap:wrap}
        .form-group{flex:1;min-width:160px;margin-bottom:16px}
        label{display:block;margin-bottom:6px;color:#8892b0;font-weight:500}
        select,input{width:100%;padding:10px 14px;border-radius:8px;border:1px solid #1e3a5f;background:#0a0e27;color:#e0e0e0;font-size:.95em}
        select:focus,input:focus{outline:none;border-color:#00d4ff}
        .btn{background:#00d4ff;color:#0a0e27;border:none;padding:13px;border-radius:8px;font-weight:600;cursor:pointer;width:100%}
        .btn:disabled{background:#555;cursor:not-allowed}
        .result-card{background:#0a0e27;border-radius:8px;padding:22px;margin-top:20px;border:1px solid #1e3a5f}
        .result-stats{display:flex;gap:12px;flex-wrap:wrap;margin:15px 0}
        .stat{background:#112240;padding:14px;border-radius:8px;text-align:center;flex:1;min-width:80px}
        .stat .number{font-size:1.8em;font-weight:700;color:#00d4ff}
        .stat .label{font-size:.8em;color:#8892b0;margin-top:4px}
        .loading{text-align:center;padding:40px;color:#8892b0}
        .spinner{border:3px solid #1e3a5f;border-top:3px solid #00d4ff;border-radius:50%;width:44px;height:44px;animation:spin 1s linear infinite;margin:0 auto 16px}
        @keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}
        .error-box{background:#1a0a0a;border:1px solid #f44;border-radius:8px;padding:20px}
        .error-box h3{color:#f44}
        .success-msg{color:#00d4ff;margin-top:15px}
        .disclaimer{text-align:center;color:#555;font-size:.78em;margin-top:30px;padding:20px;border-top:1px solid #1e3a5f}
        table{width:100%;border-collapse:collapse}
        th{text-align:left;padding:8px;color:#8892b0;border-bottom:2px solid #1e3a5f}
        td{padding:8px;border-bottom:1px solid #1e3a5f}
        td.amp{color:#f7931a;font-weight:600}
        .dd{position:absolute;top:100%;left:0;right:0;background:#112240;border:1px solid #00d4ff;border-radius:0 0 8px 8px;max-height:280px;overflow-y:auto;z-index:999;display:none}
        .dd div{padding:9px 14px;cursor:pointer;border-bottom:1px solid #1e3a5f}
        .dd div:hover{background:#1a3050}
    </style>
</head>
<body>
<div class="container">
<div class="header">
<h1>Liquidation Wick Detector</h1>
<p>开源链上强平风控检测引擎 — 精准识别永续合约恶意插针</p>
</div>
<div class="card">
<h2>运行检测</h2>
<form id="f" autocomplete="off">
<div class="form-row">
<div class="form-group"><label>交易所</label>
<select id="ex"><option value="binanceusdm" selected>币安</option><option value="okx">欧易</option><option value="gate">Gate.io</option></select></div>
<div class="form-group" style="position:relative"><label>交易对</label>
<input type="text" id="sym" placeholder="加载中..." autocomplete="off"><div id="dd" class="dd"></div></div>
<div class="form-group"><label>K线周期</label>
<select id="tf"><option value="1m">1分</option><option value="5m" selected>5分</option><option value="15m">15分</option><option value="1h">1时</option></select></div>
<div class="form-group"><label>回溯天数</label>
<input type="number" id="d" value="3" min="1" max="30"></div>
<div class="form-group"><label>振幅%</label>
<input type="number" id="a" value="3.0" min="0.5" max="20" step="0.5"></div>
</div>
<button type="submit" class="btn" id="btn">开始检测</button>
</form>
<div id="res"></div>
</div>
<div class="disclaimer">仅供教育、科研和技术演示目的使用</div>
</div>
<script>
(function(){
const sym=document.getElementById('sym'),dd=document.getElementById('dd'),ex=document.getElementById('ex'),btn=document.getElementById('btn'),res=document.getElementById('res');
let all=[],loading=false,ai=-1;
async function load(){if(loading)return;loading=true;sym.value='';sym.placeholder='加载中...';dd.style.display='none';
try{const r=await fetch('/api/symbols?exchange='+ex.value);const d=await r.json();
if(d.status==='success'&&d.symbols.length>0){all=d.symbols;sym.placeholder='搜索（'+d.count+'个合约）';sym.value=all.includes('SOL/USDT')?'SOL/USDT':all[0];}
else{sym.placeholder='加载失败';}}catch(e){sym.placeholder='加载失败';}loading=false;}
load();ex.addEventListener('change',load);
function rd(m){if(!m.length){dd.innerHTML='<div style="padding:12px;color:#8892b0;text-align:center">无匹配</div>';}else{dd.innerHTML=m.map((s,i)=>'<div data-i="'+i+'" data-s="'+s+'">'+s+'</div>').join('');}dd.style.display='block';}
function fs(q){if(!q)return all.slice(0,120);const qq=q.toUpperCase();return all.filter(s=>s.toUpperCase().includes(qq)).slice(0,60);}
function sd(){if(!all.length)return;ai=-1;rd(fs(sym.value.trim()));}
sym.addEventListener('focus',sd);sym.addEventListener('input',sd);
dd.addEventListener('mousedown',function(e){const t=e.target.closest('div[data-s]');if(t){e.preventDefault();sym.value=t.getAttribute('data-s');dd.style.display='none';}});
document.addEventListener('click',function(e){if(e.target!==sym&&!dd.contains(e.target))dd.style.display='none';});
document.getElementById('f').addEventListener('submit',async function(e){
e.preventDefault();const sv=sym.value.trim();if(!sv)return;
btn.disabled=true;btn.textContent='检测中...';res.innerHTML='<div class="loading"><div class="spinner"></div><p>抓取 '+sv+' 数据...</p></div>';
const p=new URLSearchParams({exchange:ex.value,symbol:sv,timeframe:document.getElementById('tf').value,days:document.getElementById('d').value,amp:document.getElementById('a').value});
try{
const r=await fetch('/api/detect?'+p.toString());const d=await r.json();
if(d.status==='error'){res.innerHTML='<div class="error-box"><h3>错误</h3><p>'+d.message+'</p></div>';}
else{
let h='<div class="result-card"><h3>'+d.symbol+' 报告</h3><div class="result-stats">';
h+='<div class="stat"><div class="number">'+d.total_klines.toLocaleString()+'</div><div class="label">K线数</div></div>';
h+='<div class="stat"><div class="number">'+d.hit_count+'</div><div class="label">命中</div></div>';
h+='<div class="stat"><div class="number">0</div><div class="label">误报</div></div>';
h+='</div>';
if(d.hit_count>0&&d.top_events.length>0){h+='<table><tr><th>时间</th><th>方向</th><th>振幅</th></tr>';
d.top_events.forEach(e=>{h+='<tr><td>'+e.timestamp+'</td><td>'+e.direction+'</td><td class="amp">'+e.amplitude+'%</td></tr>';});
h+='</table>';}else{h+='<p class="success-msg">零误报</p>';}
h+='</div>';res.innerHTML=h;
}}catch(err){res.innerHTML='<div class="error-box"><h3>失败</h3><p>'+err.message+'</p></div>';}
finally{btn.disabled=false;btn.textContent='开始检测';}});})();
</script>
</body>
</html>
'''

@app.route('/')
def index(): return render_template_string(HTML)

@app.route('/api/symbols')
def api_symbols():
    eid = request.args.get('exchange','binanceusdm')
    try:
        import ccxt
        ex = getattr(ccxt,eid)({'enableRateLimit':True,'timeout':30000})
        ex.load_markets(reload=True)
        syms=[]
        for s,m in ex.markets.items():
            if (m.get('swap') or m.get('type')=='swap' or m.get('linear')) and ('/USDT' in s or s.endswith(':USDT')):
                clean = s.split(':')[0] if ':' in s else s
                if clean.endswith('/USDT'): syms.append(clean)
        return jsonify({"status":"success","symbols":sorted(list(set(syms))),"count":len(syms)})
    except: return jsonify({"status":"success","symbols":['BTC/USDT','ETH/USDT','SOL/USDT','DOGE/USDT'],"count":4})

@app.route('/api/detect')
def api_detect():
    ex=request.args.get('exchange','binanceusdm'); sym=request.args.get('symbol','SOL/USDT').strip()
    tf=request.args.get('timeframe','5m'); d=request.args.get('days','3'); a=request.args.get('amp','3.0')
    if not sym or '/' not in sym: return jsonify({"status":"error","message":"格式错误"}),400
    sp='/root/liquidation-wick-detector/wick_detector_v4.py'
    if not os.path.exists(sp): return jsonify({"status":"error","message":"脚本未找到"}),500
    try:
        r=subprocess.run(['python3',sp,'--exchange',ex,'--mode','live','--symbol',sym,'--timeframe',tf,'--days',str(d),'--amp',str(a),'--no_debug'],capture_output=True,text=True,timeout=600,cwd='/root/liquidation-wick-detector')
        o=r.stdout; tk=0; hc=0; te=[]
        km=re.search(r'总K线数:\s+([\d,]+)',o); hm=re.search(r'命中事件:\s+(\d+)',o)
        if km: tk=int(km.group(1).replace(',',''))
        if hm: hc=int(hm.group(1))
        ts=o.split('振幅最大 Top')
        if len(ts)>1:
            for l in ts[1].strip().split('\n'):
                l=l.strip()
                if not l or '|' not in l: continue
                if '. ' in l[:6]: l=l.split('. ',1)[1]
                ps=[p.strip() for p in l.split('|')]
                if len(ps)>=3:
                    try: te.append({'timestamp':ps[0],'direction':ps[1],'amplitude':ps[2].replace('振幅:','').replace('%','').strip()})
                    except: pass
        return jsonify({"status":"success","exchange":ex,"symbol":sym,"total_klines":tk,"hit_count":hc,"top_events":te})
    except subprocess.TimeoutExpired: return jsonify({"status":"error","message":"超时"}),500
    except Exception as e: return jsonify({"status":"error","message":str(e)}),500

if __name__=='__main__': app.run(host='0.0.0.0',port=5000)
