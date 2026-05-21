#!/bin/bash
# WickShield 运维时间表（打印 crontab 示例，不自动安装）
# 复制到服务器: crontab -e
cat <<'CRON'
# === WickShield 运维（liquidation-wick-detector）===
# 时区: DAILY_REPORT_TZ=Asia/Shanghai

# 00:00 日切 — 清空昨日赔付计数，恢复 global_cap_remaining
0 0 * * * cd /root/liquidation-wick-detector && bash scripts/run_wickshield_reset.sh >> data/logs/wickshield_reset.log 2>&1

# 全天 — 每 5 分钟实时理赔监控（等价 live --mode full + 额度持久化）
# 监控列表：.env 或环境变量 WICKSHIELD_MONITOR_SYMBOLS=币1,币2,...
*/5 * * * * cd /root/liquidation-wick-detector && bash scripts/run_wickshield_monitor.sh >> data/logs/wickshield_monitor.log 2>&1

# 08:00 日报 — 若 Flask/gunicorn 已启动且未 DISABLE_DAILY_SCHEDULER，可省略此行
# 0 8 * * * cd /root/liquidation-wick-detector && python3 scripts/run_daily_report.py >> data/logs/daily_report.log 2>&1
CRON
