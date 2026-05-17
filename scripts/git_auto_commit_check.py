#!/usr/bin/env python3
"""检查服务器自动 git 提交/推送环境是否就绪。"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=30)


def main() -> int:
    ok = True
    print(f"项目目录: {ROOT}\n")

    r = run(["git", "rev-parse", "--is-inside-work-tree"])
    if r.returncode != 0:
        print("[FAIL] 当前目录不是 git 仓库")
        return 1
    print("[OK] Git 仓库")

    for key in ("user.name", "user.email"):
        v = run(["git", "config", key])
        val = (v.stdout or "").strip()
        if val:
            print(f"[OK] git config {key} = {val}")
        else:
            print(f"[WARN] 未设置 git config {key}（cron 提交可能失败，可设 GIT_AUTHOR_NAME/EMAIL）")
            ok = False

    remote = run(["git", "remote", "-v"])
    print("\n远程仓库:")
    print(remote.stdout or remote.stderr or "(无)")

    branch = (run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout or "").strip()
    print(f"\n当前分支: {branch}")

    fetch = run(["git", "fetch", "--dry-run", "origin", branch])
    if fetch.returncode != 0:
        print(f"[FAIL] 无法访问远程 origin（fetch 失败）:\n{fetch.stderr}")
        ok = False
    else:
        print("[OK] 可访问远程 origin")

    ssh = run(["ssh", "-o", "BatchMode=yes", "-T", "git@github.com"])
    out = (ssh.stderr or ssh.stdout or "").lower()
    if "successfully authenticated" in out or "hi " in out:
        print("[OK] GitHub SSH 认证可用")
    else:
        print("[WARN] GitHub SSH 未确认（若用 HTTPS+token 可忽略）")
        print((ssh.stderr or ssh.stdout or "")[:300])

    log_path = os.path.join(ROOT, "data", "logs", "git_auto_commit.log")
    if os.path.isfile(log_path):
        print(f"\n最近自动提交日志 ({log_path}):")
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for line in lines[-8:]:
            print(" ", line.rstrip())
    else:
        print("\n尚无 data/logs/git_auto_commit.log（日报跑完后会生成）")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
