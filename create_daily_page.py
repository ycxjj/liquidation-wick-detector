# 从 app.py 中提取每日日报的 HTML 部分，创建独立页面

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 提取 HTML 模板中的每日日报部分
# 这里需要复制检测页面的 HTML，但只保留日报部分

daily_html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>每日热门榜日报 - Wick Detector</title>
    <link rel="icon" type="image/png" href="/logo.png?v=4">
    <style>
:root{--bg:#070b1a;--card:#10192e;--border:#1e3a5f;--accent:#00d4ff;--muted:#8892b0;--text:#e8edf7;--danger:#f44;--ok:#3ecf8e}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--text);min-height:100vh;line-height:1.45}
.wrap{max-width:1100px;margin:0 auto;padding:28px 18px 60px}
.hero{text-align:center;margin-bottom:28px}
.hero h1{font-size:1.85rem;color:var(--accent);letter-spacing:-.02em}
.hero p{color:var(--muted);font-size:.95rem;margin-top:8px}
.nav-links{display:flex;gap:12px;justify-content:center;margin-bottom:28px;flex-wrap:wrap}
.nav-link{background:var(--card);border:1px solid var(--border);color:var(--muted);padding:10px 22px;border-radius:10px;text-decoration:none;font-weight:600;font-size:.92rem;transition:all 0.2s}
.nav-link:hover{border-color:var(--accent);color:var(--accent)}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:22px 20px;margin-bottom:18px}
.card h2{font-size:1.05rem;color:var(--accent);margin-bottom:14px}
.meta{color:var(--muted);font-size:.82rem;margin-bottom:14px}
.btn{background:var(--accent);color:#042;font-weight:700;border:none;padding:9px 16px;border-radius:9px;cursor:pointer;font-size:.88rem}
.back-link{color:var(--accent);text-decoration:none;display:inline-block;margin-bottom:20px}
    </style>
</head>
<body>
<div class="wrap">
    <a href="/" class="back-link">← 返回首页</a>
    <div class="hero">
        <h1>Wick Detector</h1>
        <p>每日热门榜日报 · 自选合约检测</p>
    </div>
    <div class="nav-links">
        <a href="/daily" class="nav-link" style="border-color:var(--accent);color:var(--accent);">📅 每日热门榜日报</a>
        <a href="/detect" class="nav-link">🔍 自选合约检测</a>
    </div>
    <div id="p1" class="panel show">
        <div class="card">
            <h2>📅 每日热门榜日报</h2>
            <div class="meta">自动扫描六所全市场合约，页面展示成交额前100</div>
            <div class="toolbar">
                <button class="btn" onclick="loadReport()">🔄 刷新日报</button>
            </div>
            <div id="reportContent"></div>
        </div>
    </div>
</div>
<script>
async function loadReport(){
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = '加载中...';
    try{
        const r = await fetch('/api/daily/latest');
        const d = await r.json();
        if(d.status==='success'){
            document.getElementById('reportContent').innerHTML = d.html;
        }else{
            document.getElementById('reportContent').innerHTML = '<p style="color:var(--danger);">'+d.message+'</p>';
        }
    }catch(e){
        document.getElementById('reportContent').innerHTML = '<p style="color:var(--danger);">加载失败</p>';
    }finally{
        btn.disabled = false;
        btn.textContent = '🔄 刷新日报';
    }
}
loadReport();
</script>
</body>
</html>'''

with open('templates/daily.html', 'w', encoding='utf-8') as f:
    f.write(daily_html)

print('✅ 每日日报页面已创建')
