# 🛡️ Liquidation Wick Detector

<p align="center">
  <strong>开源链上强平风控检测引擎 — 精准识别永续合约市场中的"恶意插针"</strong><br>
  为散户合约交易者建立第一道防线。
</p>

<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/version-5.1-blue.svg" alt="Version"></a>
  <a href="#"><img src="https://img.shields.io/badge/license-Apache%202.0-green.svg" alt="License"></a>
  <a href="#"><img src="https://img.shields.io/badge/Python-3.9+-yellow.svg" alt="Python"></a>
  <a href="#"><img src="https://img.shields.io/badge/exchanges-6-orange.svg" alt="Exchanges"></a>
</p>

---

## ⚠️ 项目状态说明

> **本仓库开源的是核心风控检测引擎 + WickShield v5.1 风控协议（验证阶段）。**
>
> 完整的去中心化风控协议正在开发中。
>
> **有意合作或投资请联系：** [yaochuan6666@sina.com](Tel:18911426520)

---

## 🚀 快速链接

| 功能 | 链接 |
|------|------|
| 在线演示 | [https://wickdetector.com](https://wickdetector.com) |
| 最新版 v5.1 | 本仓库 |
| 技术文档 | 见下方 README 完整文档 |

---

## 📊 核心验证数据

| 指标 | 数值 |
|------|------|
| 总验证K线数 | 100,000+ |
| 支持交易所 | 币安 / 欧易 / Gate / Bybit / Bitget / MEXC（六所聚合） |
| 动态费率币种 | 1,148+ |
| 已触发赔付事件 | 实时监控中 |

### LAB 代币专项分析（截至 2026-05-21）

| 指标 | 数值 |
|------|------|
| 近30日插针事件 | 83 次 |
| 日均插针频率 | 1.07 次/天 |
| 日均最大振幅 | 11.84% |
| 峰值振幅 | 28.99% |
| 风险评级 | **D级（极高风险）** |

> LAB 代币流通盘仅约7%，DEX定价为主，是典型的庄家控盘标的。
> 引擎精准捕获了近期多次极端插针事件，验证了检测模型的有效性。

---

## 📁 项目结构
liquidation-wick-detector/
├── app.py # Flask 主程序
├── daily_scan.py # 日报扫描（六所聚合）
├── wick_detector_v4.py # 检测引擎
├── premium_calc.py # 🆕 动态保费计算
├── payout_calc.py # 🆕 参数化赔付计算
├── solvency_check.py # 🆕 偿付能力检查
├── points_system.py # 积分系统
├── requirements.txt # Python 依赖
├── start.sh / stop.sh # 启动/停止脚本
│
├── scripts/
│ ├── wickshield/ # 🆕 WickShield v5.1 核心模块（18个文件）
│ │ ├── cli.py # 统一CLI入口
│ │ ├── monitor.py # 六所聚合实时监控
│ │ ├── premium_calc.py # 动态保费计算
│ │ ├── payout_calc.py # 参数化赔付
│ │ ├── solvency_check.py # 偿付能力检查
│ │ ├── report_risk.py # 日报风险评级
│ │ ├── market_data.py # 多交易所行情获取
│ │ ├── fetch_guard.py # API限流熔断
│ │ └── ...
│ └── run_wickshield_*.sh # 监控启动脚本
│
├── data/
│ ├── rates/ # 🆕 动态费率表
│ └── wickshield/ # 🆕 风控运行数据
│
├── templates/
│ ├── daily_report.html # 日报页面
│ ├── landing.html # 落地页
│ └── wickshield_dashboard.html # 🆕 风控看板
│
└── .env.example # 环境变量模板

text

---

## 🎯 功能特性

### 核心检测引擎
- ✅ 多维度插针检测：振幅、实体占比、影线长度、回弹比例 — 四条件联合判定
- ✅ 六所聚合监控：币安 / 欧易 / Gate / Bybit / Bitget / MEXC
- ✅ 双市场支持：现货 (Spot) + 永续合约 (Swap)
- ✅ 三种运行模式：实时监控 / 历史回溯 / CSV文件分析
- ✅ 自动数据下载：Binance公开数据 + Gate.io API
- ✅ 事件证据生成：自动输出带哈希指纹的JSON证据文件

### 🆕 WickShield v5.1 风控协议
- ✅ **动态保费计算**：三层联动（资金池健康度 + 用户信用 + 折扣叠加）
- ✅ **参数化赔付**：赔市场事件，不赔个人损失（80%固定比例）
- ✅ **偿付能力检查**：5级风险状态 + 动态投保限额调整
- ✅ **三级熔断机制**：单币种 / 全站 / 经验费率
- ✅ **CLI 工具**：premium / payout / live / solvency / backtest
- ✅ **风控看板**：实时监控 + 动态封顶 + 剩余额度

### 积分系统
- ✅ Web3 钱包登录
- ✅ 积分兑换规则管理
- ✅ 排行榜和奖励系统

---

## 📐 判定标准

| 条件 | 参数 | 含义 |
|------|------|------|
| 振幅阈值 | ≥ 1.5%~5.0%（因币种而异） | 足以导致高杠杆爆仓 |
| 极小实体 | 实体 < 振幅的50% | 价格最终回归原点 |
| 极长影线 | 影线 ≥ 实体的5倍 | 瞬时极端偏离 |
| 快速回弹 | 影线端回弹 ≥ 70% | 典型的"针形"反转 |

**当以上四个条件同时满足时，判定为"恶意插针"，触发事件通知。**

---

## 🚀 快速开始

### 安装依赖

```bash
pip install ccxt pandas numpy requests flask python-dotenv
基础检测引擎
bash
# 实时监控 SOL 永续合约近30天
python3 wick_detector_v4.py --mode live --symbol SOL/USDT --market swap --days 30

# 回溯测试 BTC 2023-08-17
python3 wick_detector_v4.py --mode backtest --symbol BTC/USDT --date 2023-08-17

# 查看帮助
python3 wick_detector_v4.py --help
🆕 WickShield v5.1 CLI 工具
bash
# 查看帮助
python3 -m scripts.wickshield.cli --help

# 计算动态保费
python3 -m scripts.wickshield.cli premium \
  --amount 1000 --symbol LAB/USDT --days 7 --leverage 10 --solvency-ratio 45

# 实时监控 + 保费报价 + 赔付测算（一键全出）
python3 -m scripts.wickshield.cli live \
  --symbol LAB/USDT --amount 1000 --exchange gate --mode full

# 偿付能力检查
python3 -m scripts.wickshield.cli solvency check --pool 50000 --coverage 100000

# 压力测试（蒙特卡洛模拟）
python3 -m scripts.wickshield.cli solvency stress --scenario severe --runs 100

# 回测
python3 -m scripts.wickshield.cli backtest --pool 50000 --coverage 100000 --json
日报系统
bash
# 运行日报扫描（六所聚合）
python3 daily_scan.py

# 启动 Web 服务
python3 app.py
📊 验证结果
实时监控（近30天 六所聚合）
币种	K线数量	误报	插针事件	风险评级
BTC/USDT	8,640+	0	0	A-（极低风险）
ETH/USDT	8,640+	0	0	A-（极低风险）
SOL/USDT	8,640+	0	0	A（低风险）
TON/USDT	8,640+	0	0	A（低风险）
LAB/USDT	8,640+	0	83	D（极高风险）
合计	100,000+	0	83	-
参数化赔付逻辑
WickShield 采用参数化保险（Parametric Insurance）模型：

赔的是"市场插针事件"，不是"用户个人爆仓"。

场景	用户亏损	赔付金额	净结果
真的爆仓	-1000U	+800U	只亏200U，回血80%
设了止损	-200U	+800U	反而赚600U
为什么用户不会故意"赌插针"来骗保？

赔付金额（80%）永远小于极端行情下的潜在损失（100%+），
没有任何理性用户会为了一笔不一定发生的赔款，去承担可能远超赔款的市场风险。

🗺️ 路线图
版本	状态	功能
v1.0-v4.0	✅ 已完成	核心检测引擎 + 多市场支持
v4.1	✅ 已完成	日报系统 + 积分系统
v5.0	✅ 已完成	六所聚合监控 + 动态费率表
v5.1	✅ 已完成	动态保费 + 参数化赔付 + 偿付能力检查
v6.0	🔄 开发中	智能合约集成（链上自动赔付）
v7.0	📋 规划中	去中心化风控协议主网上线
产品化路径
text
                  ┌──────────────────────────────────┐
                  │       WickShield v5.1             │
                  │   Python风控引擎 + CLI工具         │
                  │   六所聚合检测 + 动态保费          │
                  └───────────────┬──────────────────┘
                                  │
                                  ▼
                  ┌──────────────────────────────────┐
                  │       链上检测合约（v6.0）         │
                  │       参数化自动赔付               │
                  └───────────────┬──────────────────┘
                                  │
                                  ▼
                  ┌──────────────────────────────────┐
                  │    去中心化风控协议（v7.0）        │
                  └──────────────────────────────────┘
🤝 参与贡献
正在寻找：

合约交易者：分享你的爆仓经历，帮我们验证模型

DeFi开发者：参与智能合约和协议开发

数据科学家：优化检测算法和精算模型

联系方式： yaochuan6666@sina.com

⚠️ 免责声明
本项目（Liquidation Wick Detector）仅为教育、科研和技术演示目的而创建。

非商业用途： 本工具是一个开源的技术验证原型，旨在展示链上数据分析与风控算法的可行性。代码作者不运营、不推广、不鼓励任何将此工具用于商业风控产品、非法金融活动或任何违反当地法律法规的行为。

风险自担： 使用者因使用本工具（包括但不限于运行脚本、参考代码、使用检测结果）而产生的一切后果和风险，均由使用者自行承担。代码作者不承担任何责任。

不构成风控承诺： 本工具输出的"插针检测结果"仅为算法层面的技术判定，不构成任何形式的风控承诺、投资建议或交易指导。任何基于本工具检测结果进行的交易、风控配置、索赔行为，均与本项目及代码作者无关。

合规提醒： 使用者在运行、修改、分发本工具前，应确保自身行为符合所在国家或地区的法律法规。特别是，在中华人民共和国境内，不得将本工具用于任何涉及虚拟货币交易、代币发行、非法集资、非法经营风控产品等被法律禁止的活动。

开源协议： 本项目代码基于 Apache License 2.0 开源，但开源不等于放弃责任豁免。上述免责声明独立于开源协议之外，始终有效。

📄 许可证
Apache License 2.0

<p align="center"> <b>Building the safety net for crypto retail traders.</b><br> <i>让每一次插针，都不再以散户的爆仓为代价。</i> </p><p align="center"> <a href="https://wickdetector.com">wickdetector.com</a> </p> ```
