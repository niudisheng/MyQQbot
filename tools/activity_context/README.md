# Activity Context

这是一个给 AI 用的“本地活动上下文”工具。

它会把你的 `ActivityWatch` 记录拉到本地 SQLite，再整理成更容易读的摘要，最后通过统一入口给 AI 查询。默认设计是：

- 原始活动数据只留本地
- AI 不直接读取 ActivityWatch，而是通过 `context_api.py` 读取
- 云端如果要同步，只同步脱敏后的摘要，不同步原始窗口记录

## 这套东西能做什么

你可以把它理解成 4 步：

1. `collector.py`
   - 从 `ActivityWatch` 拉原始事件
2. `summarizer.py`
   - 把原始事件整理成“最近这段时间你可能在干嘛”
3. `context_api.py`
   - 给 AI 或你自己查询结果
4. `cloud_sync.py`
   - 把脱敏后的摘要发到云端

若你在 VPS 上部署配套接收端，见 [`cloud_server/README.md`](cloud_server/README.md)：接收与 `cloud_sync.py` 相同的 JSON、本地 SQLite 落库，并提供受 Token 保护的对外 HTTP 拉取（`/api/v1/fetch`）用于拉第三方数据并保存。

## 最简单的使用方式

先确认你已经开着 `ActivityWatch`。

然后在项目根目录执行：

```bash
python -m tools.activity_context.collector --pretty
python -m tools.activity_context.summarizer --pretty
python -m tools.activity_context.context_api focus --minutes 15 --pretty
```

这 3 步分别表示：

1. 拉取最近的活动记录
2. 生成摘要
3. 查看最近 15 分钟的当前焦点

如果输出里已经出现 `facts_text`、`inferred_task`、`data_status`，说明整条链路已经通了。

## 推荐日常使用顺序

平时建议按这个顺序用：

1. 先采集

```bash
python -m tools.activity_context.collector --pretty
```

2. 再生成摘要

```bash
python -m tools.activity_context.summarizer --pretty
```

3. 最后查询

```bash
python -m tools.activity_context.context_api health --pretty
python -m tools.activity_context.context_api focus --minutes 15 --pretty
python -m tools.activity_context.context_api recent --hours 2 --limit 10 --pretty
python -m tools.activity_context.context_api project MyQQbot --days 1 --limit 10 --pretty
```

如果你要让别的 AI 工具访问，可以开本地 HTTP 服务：

```bash
python -m tools.activity_context.context_api serve --host 127.0.0.1 --port 8765
```

启动后可用这些接口：

- `GET /health`
- `GET /focus?minutes=15`
- `GET /recent?hours=2&limit=10`
- `GET /project?name=MyQQbot&days=1&limit=10`

## 每个命令是干什么的

### `collector.py`

用途：从 `ActivityWatch` 拉原始事件，并写入本地数据库。

常用命令：

```bash
python -m tools.activity_context.collector --pretty
```

你一般会关心这些字段：

- `bucket_count`
  - 找到了几个 ActivityWatch bucket
- `event_count`
  - 本次读到了多少事件
- `inserted_count`
  - 实际写入了多少条新事件
- `health_status`
  - 当前采集状态
- `last_event_time`
  - 最近一条事件结束时间

### `summarizer.py`

用途：把原始事件整理成时间片摘要。

常用命令：

```bash
python -m tools.activity_context.summarizer --pretty
```

如果想单独看某个时间段，也可以传 `--start` 和 `--end`。

### `context_api.py`

用途：统一查询入口。

常用命令：

```bash
python -m tools.activity_context.context_api health --pretty
python -m tools.activity_context.context_api focus --minutes 15 --pretty
python -m tools.activity_context.context_api recent --hours 2 --limit 10 --pretty
python -m tools.activity_context.context_api project MyQQbot --days 1 --limit 10 --pretty
```

主要查询能力：

- `health`
  - 看采集状态是不是正常
- `focus`
  - 看最近一段时间大概在干嘛
- `recent`
  - 看最近几个时间片的摘要
- `project`
  - 看某个项目相关的时间线

### `cloud_sync.py`

用途：把脱敏后的摘要同步到云端。

先建议只用干跑模式看要发什么：

```bash
python -m tools.activity_context.cloud_sync --dry-run --pretty
```

确认内容符合预期后，再配置云端地址做真实同步。

## 怎么判断结果靠不靠谱

查询结果里最重要的几个字段：

- `facts_text`
  - 观察到的事实，比如主要应用、窗口标题、候选项目
- `inferred_task`
  - 系统推断你可能在做什么
- `confidence`
  - 推断置信度
- `data_status`
  - 当前数据完整度

`data_status` 的含义：

- `healthy`
  - 数据比较完整
- `partial`
  - 这段时间有缺口，结果只能参考
- `stale`
  - 采集器最近没拿到新数据
- `offline`
  - ActivityWatch 不可用，或者还没开始采集

简单说：

- `facts_text` 更像“看到什么”
- `inferred_task` 更像“猜你在做什么”
- `data_status` 用来告诉你“这次猜得稳不稳”

## 数据存在哪里

默认数据库路径：

```text
tools/activity_context/data/activity_context.db
```

核心表：

- `raw_events`
  - 原始 ActivityWatch 事件，只留本地
- `sync_state`
  - 采集状态、最近时间、错误信息
- `activity_summary`
  - AI 实际使用的时间片摘要
- `cloud_export_queue`
  - 等待同步到云端的摘要队列

## 如何 debug

建议按“从下到上”的顺序排：

### 1. 先看 ActivityWatch 本身是不是活着

先跑：

```bash
python -m tools.activity_context.collector --pretty
```

如果这里就失败，先别看摘要和查询。

优先检查：

- ActivityWatch 有没有启动
- `ACTIVITY_CONTEXT_AW_BASE_URL` 对不对
- 本机的 ActivityWatch 有没有 `aw-watcher-window` 和 `aw-watcher-afk`

正常情况下你应该能看到：

- `bucket_count` 大于 0
- `event_count` 大于 0
- `health_status` 是 `healthy` 或 `stale`

如果是 `offline`，通常说明：

- ActivityWatch 没开
- 地址不对
- API 请求失败

### 2. 再看摘要能不能生成

执行：

```bash
python -m tools.activity_context.summarizer --pretty
```

如果输出里一直是：

- `created_count = 0`

通常有几种可能：

- 还没有采集到任何原始事件
- 当前时间片还没到可以生成的边界
- 采集到了，但时间范围太短

这时先回去跑一次 `collector.py`，然后再试。

### 3. 再看查询是不是正常

先看健康状态：

```bash
python -m tools.activity_context.context_api health --pretty
```

再看当前焦点：

```bash
python -m tools.activity_context.context_api focus --minutes 15 --pretty
```

如果 `health` 正常，但 `focus` 很空，通常表示：

- 最近时间片还没有足够的摘要
- 当前时间段事件太少
- 数据处于 `partial`

### 4. 如果项目识别不准

比如你明明在做 `MyQQbot`，结果项目名识别偏了，可以先看：

```bash
python -m tools.activity_context.context_api focus --minutes 15 --pretty
python -m tools.activity_context.context_api recent --hours 2 --limit 10 --pretty
```

重点看：

- `facts_text` 里的窗口标题
- `project_hint`
- `tags`

因为项目识别本质上是启发式推断，不是绝对正确。

如果某个窗口标题里总是带偏系统判断，后续就应该针对这个标题模式改归因规则。

### 5. 如果云同步不对

不要一开始就真实上传，先干跑：

```bash
python -m tools.activity_context.cloud_sync --dry-run --pretty
```

你要重点确认：

- 有没有把不该上云的原始信息带出去
- `project_hint`、`task_summary`、`tags` 是否符合预期
- `missing_ranges` 是否保留了数据缺口信息

确认没问题后，再配置：

- `ACTIVITY_CONTEXT_CLOUD_SYNC_URL`
- `ACTIVITY_CONTEXT_CLOUD_SYNC_TOKEN`

## 常见问题

### 为什么 `focus` 和 `recent` 不一样

`focus` 是基于“当前最近一段时间”临时算出来的。  
`recent` 是已经落库的摘要时间片。

所以：

- `focus` 更实时
- `recent` 更稳定

### 为什么会出现 `partial`

因为 ActivityWatch 不是每一秒都一定有完整记录，或者你是中途才开始采集。

这不是报错，意思只是：

“这一段数据不完整，结论要保守看。”

### 为什么 AI 不直接读取 ActivityWatch

因为这样耦合太重，也不好做缓存、脱敏和缺失处理。

统一走 `context_api.py` 会更稳，也方便以后接多个 AI。

## 主要环境变量

- `ACTIVITY_CONTEXT_AW_BASE_URL`
  - ActivityWatch API 地址，默认 `http://127.0.0.1:5600`
- `ACTIVITY_CONTEXT_AW_BUCKET_PREFIXES`
  - 逗号分隔的 bucket 前缀，默认 `aw-watcher-window,aw-watcher-afk`
- `ACTIVITY_CONTEXT_DB_PATH`
  - SQLite 文件路径；不填则使用默认数据目录
- `ACTIVITY_CONTEXT_SUMMARY_MINUTES`
  - 摘要时间片长度，默认 `15`
- `ACTIVITY_CONTEXT_STALE_AFTER_MINUTES`
  - 超过多久未采集则视为 `stale`
- `ACTIVITY_CONTEXT_DISPLAY_TZ`
  - 摘要里「几点到几点」与时间片切分对齐的时区（IANA），默认 `Asia/Shanghai`；数据库里 `start_at`/`end_at` 仍为 UTC。Windows 若报错可 `pip install tzdata`。
- `ACTIVITY_CONTEXT_CLOUD_SYNC_URL`
  - 云端摘要接收地址；须与实际协议一致（直连 uvicorn 无 TLS 时用 `http://IP:端口/...`）
- `ACTIVITY_CONTEXT_CLOUD_SYNC_TOKEN`
  - 云端同步鉴权 token
- `ACTIVITY_CONTEXT_CLOUD_SYNC_SSL_VERIFY`
  - HTTPS 时是否校验证书，默认 `true`；自签证书内网调试可设 `false`

## 隐私边界

允许上云：

- 时间段
- `project_hint`
- `inferred_task`
- 应用标签和摘要标签
- `data_status`
- `confidence`
- `missing_ranges`

禁止上云：

- 原始窗口标题历史
- 原始事件 `payload_json`
- 文件路径原文
- URL 原文
- 邮箱等敏感文本

`cloud_sync.py` 会先做脱敏，再推送到云端。
