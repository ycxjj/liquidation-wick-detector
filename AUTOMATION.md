# Wick Detector - 自动化配置说明

## 功能概述

### 1. 每日定时扫描 ✅

**功能**：每天 08:00（Asia/Shanghai）自动扫描六大交易所的 Top100 热门合约

**配置**：
- 默认时间：每天 08:00
- 时区：Asia/Shanghai
- 扫描交易所：币安、欧易、Gate、Bybit、MEXC、Bitget

**环境变量**（可选）：
```bash
# 修改定时时间
export DAILY_CRON_HOUR=8
export DAILY_CRON_MINUTE=0

# 修改时区
export DAILY_REPORT_TZ=Asia/Shanghai

# 禁用定时任务
export DISABLE_DAILY_SCHEDULER=1
```

### 2. 自动提交 GitHub ✅

**功能**：扫描完成后自动提交数据到 GitHub

**工作流程**：
1. 扫描完成后检查是否有数据变更
2. 自动 `git add data/reports/ data/wick_daily.db`
3. 自动 `git commit -m "chore: update daily reports for YYYY-MM-DD"`
4. 自动 `git push` 到远程仓库

**前提条件**：
- 项目目录是 Git 仓库
- 已配置 Git 用户信息
- 已配置 SSH 密钥或 HTTPS 认证

**配置 Git（首次部署）**：
```bash
cd /root/liquidation-wick-detector

# 初始化 Git（如果还没有）
git init
git remote add origin https://github.com/ycxjj/liquidation-wick-detector.git

# 配置用户信息
git config user.name "Your Name"
git config user.email "yaochuan6666@sina.com"

# 配置 SSH 密钥（推荐）或使用 Personal Access Token
# 方式1：SSH（推荐）
ssh-keygen -t ed25519 -C "yaochuan6666@sina.com"
# 将 ~/.ssh/id_ed25519.pub 添加到 GitHub SSH Keys

# 方式2：Personal Access Token
# 在 GitHub 生成 token，然后：
git remote set-url origin https://YOUR_TOKEN@github.com/ycxjj/liquidation-wick-detector.git
```

## 日志查看

### 查看定时任务日志
```bash
journalctl -u wick-detector -f
```

### 查看 Git 提交日志
```bash
cd /root/liquidation-wick-detector
git log --oneline -10
```

## 手动触发

### 方式1：通过 Web 界面
访问 `https://wickdetector.com/admin`，点击"后台生成昨日日报（六所）"

### 方式2：通过命令行
```bash
cd /root/liquidation-wick-detector
python3 scripts/run_daily_report.py
```

## 故障排查

### 定时任务未运行
```bash
# 检查 APScheduler 是否安装
pip3 list | grep apscheduler

# 检查服务日志
journalctl -u wick-detector -n 50 --no-pager | grep scheduler
```

### Git 自动提交失败
```bash
# 检查 Git 配置
cd /root/liquidation-wick-detector
git config --list

# 测试 Git 推送
git push

# 查看详细错误
journalctl -u wick-detector -n 50 --no-pager | grep git
```

## 数据存储

- **JSON 报告**：`data/reports/YYYY-MM-DD/{exchange}.json`
- **SQLite 数据库**：`data/wick_daily.db`

## 更新代码

```bash
cd /root/liquidation-wick-detector
git pull
systemctl restart wick-detector
```

## 注意事项

1. **Git 认证**：确保 Git 推送不需要手动输入密码（使用 SSH 密钥或 Token）
2. **磁盘空间**：定期清理旧的日报数据
3. **API 限流**：如果频繁失败，可能是交易所 API 限流
4. **网络稳定性**：确保服务器网络稳定，能访问交易所 API 和 GitHub

## 监控建议

建议设置监控告警：
- 定时任务执行失败
- Git 推送失败
- 磁盘空间不足

可以通过 `journalctl` 或第三方监控服务实现。
