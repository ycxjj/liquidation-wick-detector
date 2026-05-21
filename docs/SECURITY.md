# 安全清理说明

## 若曾提交 `相关重要信息.txt` 或 `.env`

该文件可能包含 GitHub Token、服务器密码等。**必须从 Git 历史移除并轮换全部密钥。**

### 1. 立即轮换（在 GitHub / Vultr / Namecheap 等后台）

- [ ] GitHub Personal Access Token → Settings → Developer settings → 删除旧 token，新建
- [ ] 服务器 root/面板密码
- [ ] 域名/邮箱相关密码（若写在文件里）
- [ ] `FLASK_SECRET_KEY`（`.env` 里换随机串）
- [ ] 任何第三方 API Key

### 2. 从仓库删除敏感文件（本机）

```bash
cd liquidation-wick-detector
git rm --cached 相关重要信息.txt 2>/dev/null || true
git rm --cached gunicorn.log 2>/dev/null || true
git add .gitignore docs/SECURITY.md
git commit -m "security: remove leaked secrets file and harden gitignore"
git push
```

### 3. 从 Git 历史彻底抹掉（已 push 过才需要）

```bash
# 需安装 git-filter-repo: pip install git-filter-repo
git filter-repo --path 相关重要信息.txt --invert-paths --force
git push origin main --force
```

⚠️ `--force` 会改写远程历史，协作方需重新 clone。若无协作，建议在 GitHub 把旧 token 作废后再 force push。

### 4. 私密信息存放方式

- 仅放在服务器 `/root/liquidation-wick-detector/.env`（已在 `.gitignore`）
- 本机用密码管理器或 **不进仓库** 的 `secrets.local.txt`
- 切勿再提交 `*重要信息*`、`*密码*`、`.env` 到 GitHub

### 5. 检查是否还有泄露

```bash
git log --all --full-history -- 相关重要信息.txt
git grep -i "ghp_" $(git rev-list --all) 2>/dev/null | head
```

若 `git grep` 仍有 `ghp_` 开头字符串，说明历史里还有 token，必须做第 3 步并作废该 token。
