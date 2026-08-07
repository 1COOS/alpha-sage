# HTTP API

默认地址：`http://127.0.0.1:7777/api/v1`。

| 分组 | 接口 |
|---|---|
| 健康与门禁 | `GET /health`、`GET /system/status`、`POST /system/preflight`、`POST /system/enable`、`POST /system/pause` |
| 数据 | `POST /data/sync-history`、`GET /data/sources` |
| 证据 | `POST /evidence/url`、`POST /evidence` |
| Agent | `POST /agent/eod`、`POST /agent/intraday`、`POST /agent/attribute`、`GET /agent/runs` |
| 研究 | `GET /research`、`GET /research/{instrument_id}` |
| 组合 | `GET /portfolio`、`GET /orders`、`GET /fills` |
| 经验 | `GET /experiences`、`GET /experiences/search?q=...`、`GET/POST /feedback`、`GET /lessons` |
| 进化 | `POST /evolution/weekly`、`POST /evolution/monthly`、`GET /evolution/challengers`、批准与回滚接口 |
| 模型 | `GET/PUT /settings/model` |
| 对话 | `POST /chat`，返回 `text/event-stream` |

`POST /data/sync-history` 返回 202 并在本地后台任务中运行；进度和阻断原因通过 `GET /agent/runs` 与系统状态查看。所有关键业务对象返回其不可变 ID，便于沿版本链追踪。
