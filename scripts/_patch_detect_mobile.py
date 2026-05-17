from pathlib import Path

p = Path(__file__).resolve().parents[1] / "app.py"
t = p.read_text(encoding="utf-8")

start = t.index("function buildTable(rows){")
end = t.index("function buildTableLegacy(rows){")
new_build = """function buildTable(rows){
  __dailyRows=rows;
  return buildTableMobileCards(rows)+buildTableLegacy(rows);
}
"""
t = t[:start] + new_build + t[end:]

t = t.replace(
    "cards+='</motion-div><motion-div class=\"drc-meta\">",
    "cards+='</div><div class=\"drc-meta\">",
)
t = t.replace(
    "cards+='</div><motion-div class=\"drc-meta\">",
    "cards+='</div><div class=\"drc-meta\">",
)

# loadSym + submit + dailyBody click + renderDetectResult - add if missing
if "document.getElementById('dailyBody').addEventListener" not in t:
    anchor = "document.getElementById('onlyHits').onchange=function(){renderDailyTab();};"
    insert = anchor + """
document.getElementById('dailyBody').addEventListener('click',function(e){
  var b=e.target.closest('.drc-hit-btn');
  if(!b)return;
  var idx=parseInt(b.getAttribute('data-hit-idx'),10);
  var row=__dailyRows[idx];
  if(row&&row.hits&&row.hits.length)showHitModal(row.symbol,row.hits);
});"""
    t = t.replace(anchor, insert)

if "async function loadSym(){if(loading)return;loading=true;sym.value='';sym.placeholder='加载中…';" in t:
    t = t.replace(
        "async function loadSym(){if(loading)return;loading=true;sym.value='';sym.placeholder='加载中…';dd.style.display='none';",
        "async function loadSym(){if(loading)return;loading=true;setDetectBtnReady(false,'正在加载合约列表…');sym.value='';sym.placeholder='加载中…';dd.style.display='none';",
    )

old_tail = """  if(!defaultSym)defaultSym=all[0];
  sym.value=defaultSym;
}
else if(d.status==='error'){sym.placeholder='不支持';console.error('加载交易对失败:',d.message);showModal(d.message);}
else{sym.placeholder='加载失败';}}catch(e){sym.placeholder='加载失败';console.error('加载交易对异常:',e);}loading=false;}"""
new_tail = """  if(!defaultSym)defaultSym=all[0];
  sym.value=defaultSym;
  setDetectBtnReady(true);
}
else if(d.status==='error'){sym.placeholder='不支持';setDetectBtnReady(false,'加载失败：'+(d.message||'不支持该交易所'));console.error('加载交易对失败:',d.message);showModal(d.message);}
else{sym.placeholder='加载失败';setDetectBtnReady(false,'合约列表加载失败，请换交易所或刷新');}}catch(e){sym.placeholder='加载失败';setDetectBtnReady(false,'网络异常，请刷新后重试');console.error('加载交易对异常:',e);}loading=false;}"""
if old_tail in t:
    t = t.replace(old_tail, new_tail)

if "e.preventDefault();var sv=sym.value.trim();if(!sv)return;" in t:
    t = t.replace(
        "e.preventDefault();var sv=sym.value.trim();if(!sv)return;",
        "e.preventDefault();if(!symReady){showModal('请等待交易所合约加载完成后再开始检测');return;}var sv=sym.value.trim();if(!sv){showModal('请选择或输入交易对');return;}",
    )

if "if(d.hit_count>0&&d.top_events&&d.top_events.length){h+='<table><tr><th>时间</th>" in t:
    t = t.replace(
        "if(d.hit_count>0&&d.top_events&&d.top_events.length){h+='<table><tr><th>时间</th>",
        "if(d.hit_count>0&&d.top_events&&d.top_events.length){h+='<div class=\"table-wrap\"><table><tr><th>时间</th>",
    )
    t = t.replace(
        "d.top_events.forEach(function(ev){h+='<tr><td>'+ev.timestamp+'</td><td>'+ev.direction+'</td><td class=\"amp\">'+ev.amplitude+'%</td></tr>';});\n  h+='</table>';}",
        "d.top_events.forEach(function(ev){h+='<tr><td>'+ev.timestamp+'</td><td>'+ev.direction+'</td><td class=\"amp\">'+ev.amplitude+'%</td></tr>';});\n  h+='</table></div>';}",
    )

p.write_text(t, encoding="utf-8")
print("patched app.py")
