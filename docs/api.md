# HTTP API

默认地址：`http://127.0.0.1:7777/api/v1`。

| 分组 | 接口 |
|---|---|
| 健康与门禁 | `GET /health`、`GET /system/status`、`POST /system/preflight`、`POST /system/enable`、`POST /system/pause` |
| 数据 | `POST /data/sync-history`、`GET /data/sources` |
| 证据 | `POST /evidence/url`、`POST /evidence` |
| Agent | `POST /agent/eod`、`POST /agent/intraday`、`POST /agent/attribute`、`GET /agent/runs`、`GET /agent/runs/{run_id}` |
| 研究 | `GET /research`、`GET /research/{instrument_id}` |
| 组合 | `GET /portfolio`、`GET /orders`、`GET /fills` |
| 经验 | `GET /experiences`、`GET /experiences/search?q=...`、`GET/POST /feedback`、`GET /lessons` |
| 进化 | `POST /evolution/weekly`、`POST /evolution/monthly`、`GET /evolution/challengers`、批准与回滚接口 |
| 模型 | `GET/PUT /settings/model`、`POST /settings/model/test` |
| 对话 | `POST /chat`，返回 `text/event-stream` |

## 后台任务契约

以下接口返回 `202 Accepted`，任务进入本地单线程队列：

- `POST /data/sync-history`
- `POST /agent/eod`
- `POST /agent/intraday`
- `POST /agent/attribute`
- `POST /evolution/weekly`
- `POST /evolution/monthly`
- `POST /settings/model/test`

接收响应：

```json
{
  "run_id": "run_...",
  "kind": "EOD",
  "status": "PENDING",
  "stage": "QUEUED",
  "message": "等待前序任务完成"
}
```

同类任务已经处于 `PENDING` 或 `RUNNING` 时返回 `409`，`detail` 中包含现有 `run_id`，调用方应打开该任务而不是重复提交。`GET /agent/runs` 支持 `status`、`kind` 和 `limit` 查询参数；`GET /agent/runs/{run_id}` 返回完整阶段、进度、结果和阻断原因。

任务终态为 `COMPLETED`、`BLOCKED`、`FAILED` 或 `SKIPPED`。HTTP 202 只表示成功入队，不表示业务执行成功，调用方必须继续读取 `AgentRun.status`。高频盘中调度在队列繁忙时写入 `SKIPPED`，不会延迟补跑。

## 模型连接测试

`POST /settings/model/test` 接收与模型设置表单相同的 Base URL、API 模式、推理模型、快速模型、每日预算和可选 API Key。它使用当前请求值，不保存设置；临时 API Key 仅存在于任务闭包内，不写数据库或密钥环。API Key 为空时使用当前安全存储中的密钥。

测试任务分别真实调用两个模型。终态结果的 `checks` 仅返回角色、模型名、成功状态、耗时、请求端点、HTTP 状态、错误分类、代理 Request ID 和脱敏消息，不返回 API Key 或原始模型响应。两次调用均计入每日预算并追加到 `model_invocations`；失败调用同样保留审计。连接测试和正式模型调用统一使用 `User-Agent: alpha-sage/0.1`，Responses 请求使用最小标准载荷。

所有关键业务对象返回其不可变 ID，便于沿版本链追踪。
