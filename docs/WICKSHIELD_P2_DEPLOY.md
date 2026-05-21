# WickShield P2 部署（理赔 DB + OHLCV 缓存 / WS）

## 服务器 `.env` 片段

将以下内容追加到 `/root/liquidation-wick-detector/.env`（按实际池子/保额改数值）：

```bash
# WickShield 池子（与现有配置对齐即可）
WICKSHIELD_POOL=100000
WICKSHIELD_COVERAGE=50000
DAILY_REPORT_TZ=Asia/Shanghai

# 监控 v2（与 run_wickshield_dynamic_monitor.sh 一致）
WICKSHIELD_MONITOR_DAYS_BACK=0.3
WICKSHIELD_MONITOR_LIGHT=1
WICKSHIELD_REQUEST_TIMEOUT=3
WICKSHIELD_SKIP_TIMEOUT=1
WICKSHIELD_CIRCUIT_BREAKER=1
WICKSHIELD_RATE_LIMIT_ENABLE=1
WICKSHIELD_CLAIM_FULL_VERIFY=1

# P2-10 理赔写 SQLite（看板/统计优先读 DB）
WICKSHIELD_CLAIMS_DB=1

# P2-8 进程内 OHLCV 热缓存（同周期内减少重复 REST）
WICKSHIELD_OHLCV_CACHE=1
WICKSHIELD_OHLCV_CACHE_TTL=90

# P2-8 Binance WS（可选；先 pip install websocket-client）
# WICKSHIELD_WS_ENABLE=1

# 看板 Redis（若已装 Redis）
# WICKSHIELD_REDIS_HOST=127.0.0.1
# WICKSHIELD_REDIS_PORT=6379
```

## 部署步骤

```bash
cd /root/liquidation-wick-detector
git pull
pip install -r requirements.txt

# 历史 claims_log.jsonl → SQLite（一次性）
python3 scripts/wickshield/migrate_claims_to_db.py

# 验证监控（勿用 2>/dev/null，否则看不到报错且 stdout 可能为空）
bash scripts/wickshield_verify_p2.sh

# 或手动（单行 JSON 必须加 --compact）：
# WICKSHIELD_MONITOR_SKIP_DASHBOARD_CACHE=1 python3 -m scripts.wickshield.cli monitor --compact 2>data/logs/monitor.err | tee data/logs/monitor.json
# python3 -c "import json; d=json.load(open('data/logs/monitor.json')); print(d['duration_ms'], d.get('optimization_metrics',{}).get('live_cache_hit_count'))"

# 确认 DB 已生成
ls -la data/wickshield/claims.db
```

## Git 提交（在服务器仓库内执行）

本机工作区若无 `.git`，请在服务器执行：

```bash
cd /root/liquidation-wick-detector
git add \
  scripts/wickshield/claims_db.py \
  scripts/wickshield/ohlcv_live_cache.py \
  scripts/wickshield/ws_kline_feed.py \
  scripts/wickshield/migrate_claims_to_db.py \
  scripts/wickshield/monitor.py \
  scripts/wickshield/market_data.py \
  scripts/wickshield/fetch_guard.py \
  scripts/wickshield/dashboard_data.py \
  scripts/wickshield/claims_stats.py \
  scripts/run_wickshield_dynamic_monitor.sh \
  tests/test_claims_db.py \
  requirements.txt \
  .gitignore \
  .env.example \
  docs/WICKSHIELD_P2_DEPLOY.md

git commit -m "$(cat <<'EOF'
feat(wickshield): P2 claims SQLite and OHLCV live cache

Batch-write approved/rejected claims to SQLite while keeping jsonl audit log.
Add in-process OHLCV TTL cache and optional Binance WS feed to cut REST load.
EOF
)"

git push origin main
```

## 指标说明

`run_wickshield_dynamic_monitor.sh` 输出的 `optimization_metrics` 新增：

| 字段 | 含义 |
|------|------|
| `live_cache_hit_count` | 本周期 OHLCV 热缓存命中次数 |
| `ohlcv_live_cache` | 缓存条目数 / 新鲜条目数 |
| `ws_feed` | WS 是否运行、已收 K 线条数 |
| `claims_db_batch` | 本周期写入 DB 的理赔条数 |
