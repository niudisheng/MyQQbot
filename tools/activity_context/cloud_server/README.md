# Activity Context 云端服务

与本地 `tools/activity_context/cloud_sync.py` 配套：接收脱敏摘要并落库，同时提供**受 Token 保护**的对外 HTTP 拉取接口，把响应保存到本机 SQLite。

## 职责

1. **POST `/api/v1/summaries`**  
   与本地 `cloud_sync.py` 发送的 JSON 一致，幂等键为 `(X-Client-Id, summary_id)`，默认 `X-Client-Id` 为空即单机。

2. **GET `/api/v1/summaries`**  
   查询已入库摘要（供云端 AI 或其它服务消费）。

3. **POST `/api/v1/fetch`**  
   服务端代你请求外部 URL（如公开 API、你自己的内网网关），结果写入 `external_fetch_logs`。

4. **GET `/api/v1/fetches`**  
   查看最近对外拉取记录（不含大段 body，仅元数据）。

5. **GET `/health`**  
   健康检查，无需 Token。

## 环境变量

| 变量 | 说明 |
|------|------|
| `ACTIVITY_CONTEXT_SERVER_TOKEN` | 必填。与本地 `ACTIVITY_CONTEXT_CLOUD_SYNC_TOKEN` 保持一致。 |
| `ACTIVITY_CONTEXT_SERVER_HOST` | 默认 `0.0.0.0` |
| `ACTIVITY_CONTEXT_SERVER_PORT` | 默认 `8780` |
| `ACTIVITY_CONTEXT_SERVER_DB_PATH` | SQLite 路径；不填则用 `cloud_server/data/cloud_server.db` |
| `ACTIVITY_CONTEXT_SERVER_DATA_DIR` | 数据目录（仅在不设 `SERVER_DB_PATH` 时使用） |
| `ACTIVITY_CONTEXT_SERVER_FETCH_TIMEOUT` | 对外请求超时秒数，默认 `30` |
| `ACTIVITY_CONTEXT_SERVER_FETCH_MAX_BYTES` | 单次响应最大字节，默认 `2097152` |
| `ACTIVITY_CONTEXT_SERVER_RELOAD` | 设为 `1` 时 uvicorn 热重载（开发用） |

兼容：若只配置了 `ACTIVITY_CONTEXT_CLOUD_SYNC_TOKEN` 而未配置 `SERVER_TOKEN`，服务端也会读取该变量作为校验密钥（与 `config.ingest_token()` 一致）。

## 本地运行

在项目根目录：

```bash
pip install -r tools/activity_context/cloud_server/requirements.txt
set ACTIVITY_CONTEXT_SERVER_TOKEN=你的密钥
python -m tools.activity_context.cloud_server
```

## 本地客户端配置

`.env` 中：

```env
# 直连 uvicorn（默认端口 8780、无 TLS）时必须带端口，否则会打到 80 端口其它服务导致 404
ACTIVITY_CONTEXT_CLOUD_SYNC_URL=http://你的服务器IP:8780/api/v1/summaries
# 若前面有 Nginx 终止 HTTPS 并反代到 8780，再用 https://域名/api/v1/summaries
ACTIVITY_CONTEXT_CLOUD_SYNC_TOKEN=与云端 ACTIVITY_CONTEXT_SERVER_TOKEN 相同
```

然后执行：

```bash
python -m tools.activity_context.cloud_sync --pretty
```

## API 简要

### 接收摘要（本地 cloud_sync 已对齐）

- `POST /api/v1/summaries`
- Header: `Authorization: Bearer <token>`
- 可选: `X-Client-Id: machine-a`（多设备时区分）

### 对外拉取并落库

- `POST /api/v1/fetch`
- Header: `Authorization: Bearer <token>`
- Body 示例：

```json
{
  "url": "https://httpbin.org/get",
  "method": "GET",
  "headers": {"Accept": "application/json"},
  "label": "smoke-test"
}
```

成功时返回 `log_id` 与 `body_preview`（截断）；完整响应在数据库表 `external_fetch_logs` 的 `response_body` 字段。

## 安全说明

- 生产环境务必使用 HTTPS 反代（Nginx / Caddy），并强密码 Token。
- `/api/v1/fetch` 能访问公网任意 URL，仅应用 Token 保护；若部署在敏感网络，请结合防火墙或仅允许内网调用。

## 排查：本地 `cloud_sync` 报 HTTP 502 / 503

**含义：** 浏览器或 Nginx 能连上，但 **反代到 uvicorn 失败**（502），或 **上游暂时不可用**（503）。常见原因：**`cloud_server` 没在跑**、**监听地址/端口不对**、**Nginx `proxy_pass` 指错**。

### 第一步：先确认上游本身可用（在 VPS 本机执行）

```bash
ss -tlnp | grep 8780
curl -sS http://127.0.0.1:8780/health
```

- 若 `curl` 失败或没有监听：先启动服务（示例）：

```bash
export ACTIVITY_CONTEXT_SERVER_TOKEN=你的密钥
cd /path/to/MyQQbot
python -m tools.activity_context.cloud_server
```

或用 `screen` / `tmux` / `systemd` 常驻。

### 第二步：区分是「反代问题」还是「上游问题」

在 **你电脑** 上临时把 `.env` 里的地址改成 **直连 uvicorn**（绕过 Nginx）：

```env
ACTIVITY_CONTEXT_CLOUD_SYNC_URL=http://你的公网IP:8780/api/v1/summaries
```

再跑 `python -m tools.activity_context.cloud_sync --pretty`。

- **直连 8780 成功、走域名/443 失败** → 问题在 Nginx/Caddy/证书/防火墙，继续看下一步。
- **直连 8780 也失败** → 先解决监听与安全组（云厂商控制台放行 **入站 TCP 8780**，或只监听本机则仅允许 `127.0.0.1` 由 Nginx 访问）。

### 第三步：Nginx 最小反代示例（HTTPS 终止在 Nginx，上游仍 HTTP）

```nginx
location /api/v1/ {
    proxy_pass http://127.0.0.1:8780;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

注意：`proxy_pass` 必须是 **`http://127.0.0.1:8780`**（端口与 `ACTIVITY_CONTEXT_SERVER_PORT` 一致），不要写成带路径的错 upstream。

### 其它

- 云安全组：若只从外网访问 **443**，不必对外开放 8780，只要 **Nginx 与本机 8780 互通**即可（一般同机 `127.0.0.1` 无防火墙问题）。
- 若仍 502/503：看 Nginx `error.log`（常见为 `Connection refused` = 上游未启动）。
