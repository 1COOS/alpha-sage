# 运行与恢复

## 首次初始化

```bash
cp .env.example .env
make install
make init-db
```

`make init-db` 会执行全部 Alembic 迁移并建立：

- `paper-main`，初始现金 1,000,000 CNY；
- 默认冠军 `alpha-sage-cognition-v1`；
- A 股交易规则版本、来源注册表和安全策略；
- 默认暂停状态。

## 配置模型

推荐在设置页保存 API Key，密钥进入操作系统密钥环。无可用密钥环时，在仅本机可读的 `.env` 中设置 `OPENAI_API_KEY`。模型设置还包括 Base URL、Responses/Chat Completions 模式、推理模型、快速模型和每日请求预算。

## 初始化数据

完整同步：

```bash
make sync-history
```

小规模来源验证：

```bash
cd backend
uv run alpha-sage sync-history --years 5 --limit 10
```

同步按标的串行执行，避免 SQLAlchemy Session 和 BaoStock 登录状态并发污染。股票与 ETF 分段获取后合并去重；每个标的必须通过双源价格、上市时间和流动性门槛。

## 启用与暂停

先执行 `make preflight`。全部 PASS 后，只能由用户在界面点击“自检并启用”，或显式调用启用 API。聊天、Agent 和调度器均不能绕过该步骤。

暂停后不再产生新判断或买入。组合回撤达到 18% 时系统自动暂停新开仓；已经存在的硬风控卖出订单仍允许执行。用户手动暂停则停止全部交易动作。

## 调度时间

交易所时区为 `Asia/Shanghai`：

- 盘后研究：工作日 15:20；
- 盘中复核：09:30–11:30、13:00–14:55，每 5 分钟；
- 到期归因：工作日 15:40；
- 周度规律：周五 18:00；
- 月度挑战者：每月第一个周五 19:00。

调度任务 `coalesce=false`，误触发宽限 60 秒。电脑休眠或服务停止期间错过的盘中任务不会在恢复后补跑，因此不会补造本应发生在过去的成交。

## 恢复流程

服务恢复后：

1. 查看今日驾驶舱的最后运行和阻断原因；
2. 再次执行历史同步，以公开来源补齐可证明的数据缺口；
3. 执行 `make preflight`；
4. 检查待成交订单。系统只会使用恢复后的最新完整 5 分钟行情，不回填停机期间成交；
5. 如账户由用户手动暂停，必须再次人工启用。

## 常用命令

```bash
make dev-api
make dev-web
make preflight
make test
make lint
make build
make check
```
