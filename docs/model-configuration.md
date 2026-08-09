# 模型配置

Alpha Sage 的设置页、运行时 Agent 和“测试连接”使用同一个有效配置解析器，避免页面显示与实际调用使用不同来源。

## 配置来源

Base URL、API 模式、推理模型、快速模型和每日请求预算按以下规则解析：

1. 测试连接时使用当前表单请求中的非空值；
2. 正常运行时使用设置页已经保存到 `system_settings.model_settings` 的非空值；
3. 没有保存覆盖时读取 `.env`/进程环境中的 `OPENAI_BASE_URL`、`OPENAI_API_MODE`、`REASONING_MODEL`、`FAST_MODEL`，每日预算默认 100。

初始化只创建空的 `model_settings`，不会把默认值写入数据库并永久遮住后续 `.env` 修改。`GET /settings/model` 返回每个公开字段的来源以及 `api_key_configured`，但永不返回密钥内容。

API Key 不进入数据库。解析顺序为进程环境 `OPENAI_API_KEY`、`.env` 中的 `OPENAI_API_KEY`、操作系统密钥环。测试请求如果带临时 API Key，则只在该后台任务内覆盖上述来源；测试结束后不会保存。

## 测试连接

“测试连接”会把当前表单直接提交到 `POST /settings/model/test`，不要求先保存。推理模型和快速模型各发起一次最小 JSON 契约请求，单次超时 30 秒，SDK 自动重试关闭。

Alpha Sage 通过 New API 等 OpenAI 兼容代理调用模型时统一发送 `User-Agent: alpha-sage/0.1`。Responses 模式只发送标准的 `model`、`instructions` 和 `input`，不会附带推理模型可能不支持的采样参数。连接测试与正式研究、对话和进化任务复用同一客户端和请求构造，测试通过即代表运行时使用相同传输链路。

每个结果卡包含：

- 模型角色与模型名；
- `COMPLETED` 或 `FAILED`；
- 实际耗时；
- 失败时的 HTTP 状态、错误分类和脱敏消息。
- 请求端点以及代理返回的非敏感 Request ID。

常见分类包括 `authentication`、`permission`、`provider_blocked`、`timeout`、`network`、`rate_limit`、`model_not_found`、`bad_request`、`invalid_response` 和 `provider_error`。New API 前置代理或 WAF 返回 HTTP 403 `Your request was blocked.` 时会分类为 `provider_blocked`，并明确说明这不等同于 API Key 无效。

测试是真实模型调用，两次请求均计入每日调用预算并写入追加式 `model_invocations`。测试通过只证明当前连接、鉴权、模型名和最小结构化输出可用，不代表研究数据门禁或模拟账户启用条件已经通过。

## 安全边界

- 页面只显示 API Key 已配置或未配置，不回显密钥。
- 任务参数只记录是否提供了临时 Key，不记录 Key 内容。
- 错误消息会移除已知 Key、Bearer Token 和常见 Key/Token 参数。
- 模型原始响应不通过连接测试 API 返回。
- 模型连接测试、研究和进化调用都不能绕过数据门禁、硬风控、人工启用、20 日影子期或人工晋升。
