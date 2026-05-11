#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wick Detector Web：每日热门榜日报 + 自选合约检测
"""
import os
import re
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template_string, request, session

import daily_scan

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "wick-detector-dev-secret-change-me")
ADMIN_PATH = "/" + os.environ.get("ADMIN_PATH", "admin").strip("/")


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
.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
.btn{background:var(--accent);color:#042;font-weight:700;border:none;padding:9px 16px;border-radius:9px;cursor:pointer;font-size:.88rem}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn.ghost{background:transparent;border:1px solid var(--border);color:var(--muted)}
.chk{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:.85rem}
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
.hit-detail summary{cursor:pointer;color:var(--accent)}
.ex-tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.ex-tab{background:#0a1020;border:1px solid var(--border);color:var(--muted);padding:8px 16px;border-radius:9px;cursor:pointer;font-size:.85rem;font-weight:600}
.ex-tab.on{border-color:var(--accent);color:var(--accent)}
.modal-mask{display:none;position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:1000;align-items:center;justify-content:center;padding:20px}
.modal-mask.show{display:flex}
.modal-card{background:var(--card);border:1px solid var(--accent);border-radius:16px;padding:26px 22px;max-width:440px;width:100%;text-align:center;box-shadow:0 24px 80px rgba(0,0,0,.55)}
.modal-card p{color:var(--text);font-size:.93rem;line-height:1.6;margin-bottom:20px;white-space:pre-wrap}
    </style>
</head>
<body>
<div id="modalMask" class="modal-mask" role="dialog" aria-modal="true">
  <div class="modal-card">
    <p id="modalMsg"></p>
    <button type="button" class="btn block" id="modalOk">知道了</button>
  </div>
</div>
<div class="wrap">
  <div class="hero">
    <h1>Wick Detector</h1>
    <p>每日热门合约插针日报 · 自选永续合约检测</p>
  </div>
  <div class="tabs">
    <button type="button" class="tab on" id="tabP1" data-p="p1">昨日热门 Top100 日报</button>
    <button type="button" class="tab" id="tabP2" data-p="p2">自选合约检测</button>
  </div>

  <div id="p1" class="panel show">
    <div class="card">
      <h2>热门榜插针概览 <span class="badge" id="jobBadge">—</span></h2>
      <p class="meta">每日 <strong>08:00（Asia/Shanghai）</strong> 依次对 <strong>币安 / 欧易 / Gate</strong> 三家 USDT 永续市场分别取 <strong>24h 成交额 Top100</strong>，
      对<strong>前一自然日</strong>整日 K 线做插针扫描（规则与下方自选一致）。下方分 Tab 查看各所数据；首次可手动触发生成。</p>
      <div class="toolbar">
        <button type="button" class="btn ghost" id="btnRefreshDaily">刷新数据</button>
        {% if is_admin %}<button type="button" class="btn" id="btnRunDaily">后台生成昨日日报（三所）</button>{% endif %}
        <label class="chk"><input type="checkbox" id="onlyHits"> 仅显示有插针命中</label>
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
            <select id="ex"><option value="binanceusdm" selected>币安</option><option value="okx">欧易</option><option value="gate">Gate.io</option></select></div>
          <div class="form-group" style="position:relative"><label>交易对</label>
            <input type="text" id="sym" placeholder="加载中…" autocomplete="off"><div id="dd" class="dd"></div></div>
          <div class="form-group"><label>K线周期</label>
            <select id="tf"><option value="1m">1分</option><option value="5m" selected>5分</option><option value="15m">15分</option><option value="1h">1时</option></select></div>
          <div class="form-group"><label>回溯天数</label>
            <input type="number" id="d" value="3" min="1" max="30"></div>
          <div class="form-group"><label>振幅%</label>
            <input type="number" id="a" value="3.0" min="0.5" max="20" step="0.5"></div>
        </div>
        <button type="submit" class="btn block" id="btn">开始检测</button>
        <p class="hint">拉取交易所数据并本地扫描，通常需 <strong>1～数分钟</strong>。请勿刷新页面。</p>
      </form>
      <div id="res"></div>
    </div>
  </div>

  <p class="disclaimer">仅供教育、科研和技术演示目的使用</p>
</div>
<script>
(function(){
var sym=document.getElementById('sym'),dd=document.getElementById('dd'),ex=document.getElementById('ex'),btn=document.getElementById('btn'),res=document.getElementById('res');
var all=[],loading=false,pollT=null,prevJobRunning=false;
var EX_NAMES={binanceusdm:'币安 USDM',okx:'欧易',gate:'Gate.io'};
var __reports=null,currentDailyEx='binanceusdm';

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

function tab(id){
  document.querySelectorAll('.tab').forEach(function(t){t.classList.toggle('on',t.getAttribute('data-p')===id);});
  document.getElementById('p1').classList.toggle('show',id==='p1');
  document.getElementById('p2').classList.toggle('show',id==='p2');
  if(id==='p1'){fillDatePick();loadDaily();startPoll();}else{stopPoll();}
}
document.getElementById('tabP1').onclick=function(){tab('p1');};
document.getElementById('tabP2').onclick=function(){tab('p2');};

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
        if(d.last_error){el.textContent='上次异常';el.title=d.last_error;}
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
  var h='<div class="table-wrap"><table><thead><tr><th>#</th><th>合约</th><th>24h额</th><th>K线</th><th>命中</th><th>最大振幅</th><th>摘要</th></tr></thead><tbody>';
  rows.forEach(function(r){
    var hit=r.hit_count>0;
    h+='<tr class="'+(hit?'row-hit':'')+'"><td class="num">'+r.rank_vol+'</td><td class="sym">'+r.symbol+'</td><td class="num">'+fmtVol(r.quote_volume)+'</td>';
    h+='<td class="num">'+(r.total_klines||0)+'</td><td class="num">'+(r.hit_count||0)+'</td><td class="amp">'+(r.max_amplitude!=null?r.max_amplitude+'%':'—')+'</td><td>';
    if(r.error){
      h+='<span class="err" title="'+r.error.replace(/"/g,'&quot;')+'">数据异常</span>';
    }
    else if(r.hits&&r.hits.length){
      h+='<details class="hit-detail"><summary>'+r.hits.length+' 条事件</summary><div class="details">';
      r.hits.forEach(function(e){h+=e.timestamp+' '+e.direction+' '+e.amplitude+'%<br>';});
      h+='</div></details>';
    }else{h+='—';}
    h+='</td></tr>';
  });
  return h+'</tbody></table></div>';
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
      showModal('已在后台开始依次扫描「币安 → 欧易 → Gate」昨日热门 Top100。\n请勿重复点击；进度见标题旁徽章与本页橙色提示。');
      startPoll();
    }else{
      showModal(d.message||'未启动（可能已有任务在运行）');
    }
  }).finally(function(){b.disabled=false;});
};
{% endif %}

async function loadSym(){if(loading)return;loading=true;sym.value='';sym.placeholder='加载中…';dd.style.display='none';
try{var r=await fetch('/api/symbols?exchange='+ex.value);var d=await r.json();
if(d.status==='success'&&d.symbols.length>0){all=d.symbols;sym.placeholder='搜索（'+d.count+'个合约）';sym.value=all.includes('SOL/USDT')?'SOL/USDT':all[0];}
else{sym.placeholder='加载失败';}}catch(e){sym.placeholder='加载失败';}loading=false;}
loadSym();ex.addEventListener('change',loadSym);
function rd(m){if(!m.length){dd.innerHTML='<div style="padding:10px;color:#8892b0;text-align:center">无匹配</div>';}else{dd.innerHTML=m.map(function(s,i){return '<div data-s="'+s+'">'+s+'</div>';}).join('');}dd.style.display='block';}
function fs(q){if(!q)return all.slice(0,120);var qq=q.toUpperCase();return all.filter(function(s){return s.toUpperCase().indexOf(qq)>=0;}).slice(0,60);}
function sd(){if(!all.length)return;rd(fs(sym.value.trim()));}
sym.addEventListener('focus',sd);sym.addEventListener('input',sd);
dd.addEventListener('mousedown',function(e){var t=e.target.closest('div[data-s]');if(t){e.preventDefault();sym.value=t.getAttribute('data-s');dd.style.display='none';}});
document.addEventListener('click',function(e){if(e.target!==sym&&!dd.contains(e.target))dd.style.display='none';});

document.getElementById('f').addEventListener('submit',async function(e){
e.preventDefault();var sv=sym.value.trim();if(!sv)return;
var elapsedTimer=null,sec=0;
btn.disabled=true;btn.textContent='检测中…';
res.innerHTML='<div class="loading"><div class="spinner"></div><p>抓取 '+sv+' …</p><div class="progress-wrap"><div class="progress-track"><div class="progress-indet"></div></div></div><p class="elapsed-big" id="elapsedBig">0</p><p class="hint" id="elapsed">已等待 0 秒</p></div>';
elapsedTimer=setInterval(function(){sec++;var el=document.getElementById('elapsed'),big=document.getElementById('elapsedBig');if(big)big.textContent=sec+' 秒';if(el)el.textContent='已等待 '+sec+' 秒';},1000);
var p=new URLSearchParams({exchange:ex.value,symbol:sv,timeframe:document.getElementById('tf').value,days:document.getElementById('d').value,amp:document.getElementById('a').value});
try{
var r=await fetch('/api/detect?'+p.toString());var d=await r.json();
if(d.status==='error'){res.innerHTML='<div class="error-box"><h3>错误</h3><p>'+d.message+'</p></div>';}
else{
var h='<div class="result-card"><h3>'+d.symbol+' 报告</h3><div class="result-stats">';
h+='<div class="stat"><span class="n">'+d.total_klines.toLocaleString()+'</span><div class="lb">K线</div></div>';
h+='<div class="stat"><span class="n">'+d.hit_count+'</span><div class="lb">命中</div></div>';
h+='<div class="stat"><span class="n">0</span><div class="lb">误报</div></div></div>';
if(d.hit_count>0&&d.top_events&&d.top_events.length){h+='<table><tr><th>时间</th><th>方向</th><th>振幅</th></tr>';
d.top_events.forEach(function(ev){h+='<tr><td>'+ev.timestamp+'</td><td>'+ev.direction+'</td><td class="amp">'+ev.amplitude+'%</td></tr>';});
h+='</table>';}else{h+='<p style="color:var(--accent);margin-top:8px">未发现命中</p>';}
h+='</div>';res.innerHTML=h;
}}catch(err){res.innerHTML='<div class="error-box"><h3>失败</h3><p>'+(err&&err.message?err.message:'请求失败')+'</p></div>';}
finally{if(elapsedTimer)clearInterval(elapsedTimer);btn.disabled=false;btn.textContent='开始检测';}});

fillDatePick();
loadDaily();
startPoll();
})();
</script>
</body>
</html>
'''


@app.route("/")
def index():
    session["is_admin"] = False
    return render_template_string(HTML, is_admin=False)


@app.route(ADMIN_PATH)
def admin_index():
    session["is_admin"] = True
    return render_template_string(HTML, is_admin=True)


@app.route("/api/symbols")
def api_symbols():
    eid = request.args.get("exchange", "binanceusdm")
    try:
        import ccxt

        ex = getattr(ccxt, eid)({"enableRateLimit": True, "timeout": 30000})
        ex.load_markets(reload=True)
        syms = []
        for s, m in ex.markets.items():
            if (m.get("swap") or m.get("type") == "swap" or m.get("linear")) and (
                "/USDT" in s or s.endswith(":USDT")
            ):
                clean = s.split(":")[0] if ":" in s else s
                if clean.endswith("/USDT"):
                    syms.append(clean)
        return jsonify(
            {"status": "success", "symbols": sorted(list(set(syms))), "count": len(syms)}
        )
    except Exception:
        return jsonify(
            {
                "status": "success",
                "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"],
                "count": 4,
            }
        )


@app.route("/api/detect")
def api_detect():
    ex = request.args.get("exchange", "binanceusdm")
    sym = request.args.get("symbol", "SOL/USDT").strip()
    tf = request.args.get("timeframe", "5m")
    d = request.args.get("days", "3")
    a = request.args.get("amp", "3.0")
    if not sym or "/" not in sym:
        return jsonify({"status": "error", "message": "格式错误"}), 400
    base = os.path.dirname(os.path.abspath(__file__))
    sp = os.path.join(base, "wick_detector_v4.py")
    if not os.path.exists(sp):
        return jsonify({"status": "error", "message": "脚本未找到: " + sp}), 500
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
        o = r.stdout.strip()
        if not o:
            return jsonify(
                {"status": "error", "message": "脚本无输出", "stderr": r.stderr[:500]}
            )
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


def init_app():
    daily_scan._ensure_db()
    _start_scheduler()


init_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
