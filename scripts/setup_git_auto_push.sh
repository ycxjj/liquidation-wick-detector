#!/bin/bash
# 一次性配置：让日报扫描后能自动 git commit + push
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "错误: $ROOT 不是 git 仓库"
  exit 1
fi

GIT_NAME="${GIT_AUTHOR_NAME:-wickdetector-bot}"
GIT_EMAIL="${GIT_AUTHOR_EMAIL:-bot@wickdetector.com}"

git config user.name "$GIT_NAME"
git config user.email "$GIT_EMAIL"

mkdir -p data/logs data/reports
touch data/.gitkeep 2>/dev/null || true

echo "已设置 git user.name=$GIT_NAME"
echo "已设置 git user.email=$GIT_EMAIL"
echo ""
echo "请确认能推送（任选一种）："
echo "  SSH:  ssh -T git@github.com"
echo "  HTTPS: git remote -v  且已配置 Personal Access Token"
echo ""
echo "诊断: python3 scripts/git_auto_commit_check.py"
echo "日志: tail -f data/logs/git_auto_commit.log"
