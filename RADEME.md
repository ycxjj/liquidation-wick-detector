\# 🛡️ Liquidation Wick Detector



> \\\*\\\*开源链上强平保险风控引擎 — 精准识别永续合约市场中的"恶意插针"\\\*\\\*

>

> 为散户合约交易者建立第一道防线。



\[!\[Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)

\[!\[License](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)

\[!\[Status](https://img.shields.io/badge/Status-MVP-orange.svg)](https://github.com/ycxjj/liquidation-wick-detector)



> ⚠️ \\\*\\\*项目状态说明\\\*\\\*：本仓库开源的是核心风控检测引擎（MVP验证阶段）。完整的去中心化保险协议（含精算模型、代币经济、智能合约）正在开发中。有意合作或投资请联系 \\\[联系方式]。



\---



\## 📖 背景



在加密货币永续合约市场中，"插针"是导致散户意外爆仓的主要原因——价格在极短时间内被恶意拉至极端价位，触发大量强平后迅速回归。



\*\*现有的交易所保险基金只兜底穿仓亏损，不对散户因插针导致的强平损失做任何补偿。\*\*



本项目旨在构建一个去中心化强平保险协议。本仓库是该协议的\*\*核心风控检测引擎\*\*。



\---



\## 🎯 功能



\- ✅ 多维度插针检测：振幅、实体占比、影线长度、回弹比例 — 四条件联合判定

\- ✅ 双市场支持：现货 (Spot) + 永续合约 (Swap)

\- ✅ 三种运行模式：实时监控 / 历史回溯 / CSV文件分析

\- ✅ 自动数据下载：Binance公开数据 + Gate.io API

\- ✅ 多交易所交叉验证框架

\- ✅ 调试分析：自动解析振幅最大K线的判定逻辑



\---



\## 🚀 快速开始



```bash

pip install ccxt pandas numpy requests



\\# 实时监控 SOL 永续合约近30天

python3 wick\\\_detector\\\_v4.py --mode live --symbol SOL/USDT --market swap --days 30



\\# 回溯测试 BTC 2023-08-17

python3 wick\\\_detector\\\_v4.py --mode backtest --symbol BTC/USDT --date 2023-08-17



\\# 查看帮助

python3 wick\\\_detector\\\_v4.py --help

📐 判定标准

条件	参数	含义

振幅阈值	≥ 1.5%\\\~5.0%（因币种而异）	足以导致高杠杆爆仓

极小实体	实体 < 振幅的50%	价格最终回归原点

极长影线	影线 ≥ 实体的5倍	瞬时极端偏离

快速回弹	影线端回弹 ≥ 70%	典型的"针形"反转

📊 验证结果

实时监控（近30天 Gate.io 永续合约）

币种	K线数	误报	命中

SOL	8,640	0	0

ETH	8,640	0	0

BTC	8,640	0	0

合计	25,920	0	0

2023-08-17 BTC暴跌回溯

指标	数值

当日最大振幅	8.77%

判定结果	❌ 非插针（实体占振幅98.9%，真实单边暴跌）

📁 项目结构

text

├── wick\\\_detector\\\_v4.py          # 主检测引擎

├── scripts/

│   ├── backtest\\\_wick.py         # 回溯测试脚本

│   └── download\\\_binance\\\_public.py # 数据下载工具

├── data/                        # 示例数据

├── examples/                    # 运行截图

├── docs/                        # 文档

├── README.md

└── LICENSE

🗺️ 路线图

v1.0-v4.0：核心检测引擎 + 多市场支持



v5.0：实时流式监控 + Web仪表盘



v6.0：智能合约集成（链上自动理赔触发）



v7.0：去中心化保险协议主网上线



🤝 参与贡献

正在寻找：



合约交易者：分享你的爆仓经历，帮我们验证模型



DeFi开发者：参与智能合约和协议开发



数据科学家：优化检测算法和精算模型



联系方式：\\\[yaochuan6666@sina.com]



📄 许可证

Apache License 2.0



<p align="center"> <b>Building the safety net for crypto retail traders.</b><br> <i>让每一次插针，都不再以散户的爆仓为代价。</i> </p> ```


