# WickShield 功能全景、测试矩阵与待实现清单

> 运行全部 WickShield 测试：`pytest tests/test_wickshield.py tests/test_claims_db.py tests/test_wickshield_flow_matrix.py tests/test_wickshield_api.py -v`

---

## 一、已实现功能（按模块）

### 1. 精算引擎（白盒核心）

| 功能 | 模块 | CLI | 说明 |
|------|------|-----|------|
| 保费 v3 | `premium_calc.py` | `premium` | 日报风险档、动态封顶、ATR/插针、偿付、信用折扣、当月赔付附加费 |
| 赔付 | `payout_calc.py` | `payout` | 振幅比例赔、单人/24h 上限、偿付折减 |
| 有序清算 Haircut | `payout_calc.py` | `haircut` | 池子不足时按比例砍赔付（**未接入 monitor**） |
| 偿付分类 | `solvency_check.py` | `solvency check` | healthy/watch/warning/danger/emergency |
| 压力测试 | `solvency_check.py` | `solvency stress` | mild/severe/all + 可复现 seed |
| 回测报告 | `backtest_runner.py` | `backtest` | 串联偿付→保费→赔付→压力→haircut 样例 |

### 2. 日报驱动定价

| 功能 | 模块 | CLI | 说明 |
|------|------|-----|------|
| 单币风险画像 | `report_risk.py` | `reports profile` | 读 `data/reports/YYYY-MM-DD/*.json` |
| A-/A/A+ 子档 | `report_risk.py` | — | 定价系数 |
| 费率表缓存 | `rate_table.py` | `reports refresh` | `data/rates/dynamic_rates.json` |
| 日报后自动刷新 | `daily_scan.py` 钩子 | — | `DISABLE_RATE_TABLE_REFRESH=1` 可关 |

### 3. 实时行情与监控

| 功能 | 模块 | 说明 |
|------|------|------|
| 六所 K 线聚合 | `market_data.py` | 取最大 1h 振幅、最多插针次数 |
| 轻量监控 | `market_data.py` | `WICKSHIELD_MONITOR_LIGHT=1` |
| 拉取防护 | `fetch_guard.py` | 超时、熔断、限频、降级缓存 |
| OHLCV 热缓存 P2 | `ohlcv_live_cache.py` | TTL 命中跳过 REST |
| Binance WS P2 | `ws_kline_feed.py` | 可选，`WICKSHIELD_WS_ENABLE=1` |
| 并行 monitor | `monitor.py` | 多币并行拉行情，决策串行保额度正确 |
| 赔付前全量复核 | `claim_verify.py` | 轻量触发后 wick_detector 二次确认 |
| 动态 Worker | `fetch_guard.py` | 凌晨 4 / 白天 8 / 其余 6 |

### 4. 理赔与台账（自动判定，非自动打款）

| 功能 | 存储 | 说明 |
|------|------|------|
| 决策流水线 | `monitor.run_live_check` | no_trigger / error / rejected / approved |
| 今日已赔累计 | `monitor_state.json` | 仅 approved 且非 dry-run |
| 审计日志 | `claims_log.jsonl` | approved + rejected |
| SQLite P2 | `claims.db` | 批量写入、看板/统计优先读 |
| 当月附加费统计 | `claims_stats.py` | approved 笔数 → 保费乘数 |
| 日切重置 | `reset` / `run_wickshield_reset.sh` | 00:00 清零 global_payout_today |
| 链上占位 | `_notify_chain` | `WICKSHIELD_CHAIN_WEBHOOK` POST JSON |

### 5. 运维脚本（Cron）

| 脚本 | 建议周期 |
|------|----------|
| `run_wickshield_reset.sh` | 每天 00:00 |
| `run_wickshield_dynamic_monitor.sh` | 每 5 分钟 |
| `run_wickshield_refresh_top50.sh` | 按需（热门币单） |
| `wickshield_verify_p2.sh` | 部署后手工验证 |

### 6. 看板与 API（只读）

| 路由 | 说明 |
|------|------|
| `GET /wickshield` | HTML 看板 |
| `GET /api/wickshield/dashboard?live=0\|1` | JSON：偿付、额度、监控表、最近理赔 |
| `monitor_cache.py` | Redis 或内存 TTL |

### 7. 主应用（非 WickShield 保险，但同仓库）

- 日报扫描 `/daily`、`daily_scan.py`、`data/wick_daily.db`
- 插针检测 `/detect`、`wick_detector_v4.py`
- 积分/Web3：`points_system.py`、钱包登录、任务、兑换等

---

## 二、理赔决策状态机（已实现细节）

```
行情拉取 → 振幅 vs 阈值
  ├─ 未达阈值 → no_trigger（不落理赔表）
  ├─ 赔付计算失败 → error
  ├─ 偿付 blocked → rejected（暂停赔付）
  ├─ 未触发 → no_trigger
  ├─ final_payout≤0 → rejected（24h 用尽 / 单人上限 / 折减为 0）
  ├─ 否则 → approved（暂定）
  │     └─ light_mode + 全量复核开启 → verify
  │           ├─ verified=false → rejected（你截图里多数情况）
  │           └─ verified=true → approved
  └─ approved + 非 dry-run → 累加 global_payout_today + jsonl + db + webhook?
```

**不是自动打款**：无用户钱包绑定、无链上转账；仅有内部记账 + 可选 Webhook。

---

## 三、测试矩阵（流程 ID → 测试类）

| ID | 流程 | 类型 | 测试位置 |
|----|------|------|----------|
| F01 | 保费计算全分支 | 白盒 | `test_wickshield.py` + `TestFlowPremium` |
| F02 | 赔付计算全分支 | 白盒 | 同上 + `TestFlowPayout` |
| F03 | Haircut 分配 | 白盒 | 同上 + `TestFlowHaircut` |
| F04 | 偿付 check/stress | 白盒 | 同上 + `TestFlowSolvency` |
| F05 | 日报风险/费率表 | 白盒 | `TestReportRisk` + `TestFlowReports` |
| F06 | CLI 全命令 JSON 输出 | **黑盒** | `TestFlowCLIBlackbox` |
| F07 | monitor 干跑周期 | 白盒+集成 | `TestFlowMonitorCycle` |
| F08 | 决策状态机各分支 | 白盒 | `TestFlowDecisionMachine` |
| F09 | 赔付前复核 | 白盒 | `TestMonitorPerf` + `TestFlowClaimVerify` |
| F10 | 日切 reset | 白盒 | `TestDailyReset` + `TestFlowReset` |
| F11 | claims_db 读写 | 白盒 | `test_claims_db.py` + `TestFlowClaimsDb` |
| F12 | dashboard 快照字段 | 白盒 | `TestDashboard` + API 黑盒 |
| F13 | fetch_guard 熔断/缓存 | 白盒 | `TestFlowFetchGuard` |
| F14 | OHLCV 热缓存 TTL | 白盒 | `TestFlowOhlcvCache` |
| F15 | chain webhook POST | 白盒 | `TestFlowChainWebhook` |
| F16 | Flask 看板 API | **黑盒** | `test_wickshield_api.py` |
| F17 | dry-run 不写额度 | 白盒 | `TestFlowMonitorCycle` |
| F18 | 热门币单 symbols refresh | 黑盒/网络 | 默认 skip，mock 可选 |

---

## 四、待实现 / 占位（未做或仅半成品）

| 优先级 | 功能 | 现状 |
|--------|------|------|
| P0 产品 | 用户投保/绑保单 | 无 API；monitor 用全局 `WICKSHIELD_MONITOR_AMOUNT` |
| P0 产品 | 用户收款/链上赔付 | 仅 `WICKSHIELD_CHAIN_WEBHOOK` 占位 POST |
| P1 | monitor 批准时 Haircut 排队 | 仅有 CLI `haircut`，池子不足直接 reject |
| P1 | 每用户/每钱包保额与理赔 | 全站共用池与 24h  cap |
| P2 | 信用分/产品线 tier 与账户打通 | CLI 支持，monitor 固定默认 |
| P2 | 用户主动报案 API | 仅系统自动扫描产生理赔 |
| P2 | Webhook 消费端 / 合约 | 仓库外 |
| P3 | WS 覆盖六所 | 仅 Binance 5m |
| P3 | 理赔人工审核台 | 无 |

---

## 五、推荐测试命令

```bash
# 全量 WickShield
pytest tests/test_wickshield.py tests/test_claims_db.py \
  tests/test_wickshield_flow_matrix.py tests/test_wickshield_api.py -v

# 仅快速白盒（无 Flask）
pytest tests/test_wickshield.py tests/test_claims_db.py tests/test_wickshield_flow_matrix.py -q

# 服务器部署后冒烟
bash scripts/wickshield_verify_p2.sh
```
