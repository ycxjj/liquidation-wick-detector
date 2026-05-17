# Cloudflare 防火墙与限流规则（建议）

在 Cloudflare Dashboard → Security → WAF / Rate limiting 中配置。

## 1. 基础防护

| 规则 | 表达式 | 动作 |
|------|--------|------|
| 阻止非标准方法 | `not http.request.method in {"GET" "POST" "HEAD" "OPTIONS"}` | Block |
| 保护管理路径 | `(http.request.uri.path contains "/admin")` | Challenge 或 IP 白名单 |
| 国家/地区（可选） | 按业务需要 | Managed Challenge |

## 2. 速率限制（与 Nginx / Redis 对齐）

| 名称 | 路径 | 阈值 |
|------|------|------|
| Auth API | `/api/points/login` 或 `/api/points/nonce` | 20 req / 5 min / IP |
| Detect API | `/api/detect*` | 60 req / 5 min / IP |
| General API | `/api/*` | 240 req / 5 min / IP |

Cloudflare Rate limiting 示例表达式：

```
(http.request.uri.path eq "/api/points/login")
```

## 3. Bot 与扫描

- 开启 **Bot Fight Mode**（免费版）或 **Super Bot Fight Mode**（Pro+）
- 对 `User-Agent` 为空或常见扫描器 UA 使用 Block 自定义规则

## 4. 与源站配合

- 源站仅允许 Cloudflare IP（可选，见 Cloudflare IP ranges）
- 确保 `CF-Connecting-IP` 或 `X-Forwarded-For` 正确传递到 Flask（`client_ip()` 已读取 `X-Forwarded-For`）

## 5. 环境变量（应用层）

```bash
REDIS_URL=redis://127.0.0.1:6379/0
WITHDRAWAL_COOLDOWN_HOURS=72
ADMIN_ADDRESSES=0xYourAdminWallet
```
