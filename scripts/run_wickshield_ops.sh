#!/bin/bash
# 打印推荐 crontab（不自动安装）: bash scripts/run_wickshield_ops.sh
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cat <<EOF
# === WickShield 运维（liquidation-wick-detector）===
# 时区: DAILY_REPORT_TZ=Asia/Shanghai

# 00:00 日切
0 0 * * * cd ${ROOT} && bash scripts/run_wickshield_reset.sh >> data/logs/wickshield_reset.log 2>&1

# 每 5 分钟监控（只保留 dynamic，勿重复 monitor.sh）
*/5 * * * * cd ${ROOT} && bash scripts/run_wickshield_dynamic_monitor.sh >> data/logs/wickshield_monitor.log 2>&1

# 08:00 日报（若 Flask 已调度可注释）
# 0 8 * * * cd ${ROOT} && python3 scripts/run_daily_report.py >> data/logs/daily_report.log 2>&1
EOF
