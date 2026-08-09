"use client";

import { FormEvent, useEffect, useState } from "react";
import { API_BASE, api, money, percent } from "@/lib/api";
import type {
  ActionRunner,
  ActionFeedbackMap,
  ActionFeedbackSummary,
  AgentRun,
  BusyActions,
  Challenger,
  Experience,
  Fill,
  Lesson,
  Order,
  Portfolio,
  Research,
  SourceHealth,
  SystemStatus,
} from "./types";

export function TodayView({
  status,
  portfolio,
  research,
  onAction,
  busyActions,
  feedbackByAction,
  onOpenTaskCenter,
}: {
  status: SystemStatus | null;
  portfolio: Portfolio | null;
  research: Research[];
  onAction: ActionRunner;
  busyActions: BusyActions;
  feedbackByAction: ActionFeedbackMap;
  onOpenTaskCenter: () => void;
}) {
  const cashRatio = portfolio
    ? Number(portfolio.cash) / Number(portfolio.equity || 1)
    : 0;
  return (
    <>
      <div className="metric-grid">
        <Metric label="账户权益" value={money(portfolio?.equity)} meta="CNY / PAPER" />
        <Metric
          label="可用现金"
          value={money(portfolio?.cash)}
          meta={`现金占比 ${percent(cashRatio)}`}
        />
        <Metric
          label="当前回撤"
          value={percent(portfolio?.drawdown)}
          meta={portfolio?.risk_state ?? "—"}
          tone={Number(portfolio?.drawdown ?? 0) >= 0.12 ? "risk" : "normal"}
        />
        <Metric label="已归档研究" value={String(research.length)} meta="最近 40 份" />
      </div>

      <div className="dashboard-grid">
        <article className="panel span-2">
          <PanelHead
            title="今日运行链"
            meta="OBSERVE → RESEARCH → OPPOSE → DECIDE → TRADE"
          />
          <div className="run-chain">
            {[
              "数据与日历",
              "机会发现",
              "深度研究",
              "反方质疑",
              "组合风控",
              "5分钟执行",
              "归因学习",
            ].map((label, index) => (
              <div
                className={status?.blockers.length && index === 0 ? "run-step blocked" : "run-step"}
                key={label}
              >
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{label}</strong>
              </div>
            ))}
          </div>
          <div className="button-row">
            <button
              className="primary"
              disabled={busyActions.has("盘后研究")}
              onClick={() =>
                void onAction("盘后研究", () =>
                  api("/agent/eod", { method: "POST" }),
                )
              }
            >
              {busyActions.has("盘后研究") ? "盘后研究执行中…" : "运行盘后研究"}
            </button>
            <button
              className="ghost"
              disabled={busyActions.has("盘中复核")}
              onClick={() =>
                void onAction("盘中复核", () =>
                  api("/agent/intraday", { method: "POST" }),
                )
              }
            >
              {busyActions.has("盘中复核") ? "盘中复核执行中…" : "运行盘中复核"}
            </button>
            <button
              className="ghost"
              disabled={busyActions.has("经验归因")}
              onClick={() =>
                void onAction("经验归因", () =>
                  api("/agent/attribute", { method: "POST" }),
                )
              }
            >
              {busyActions.has("经验归因") ? "归因执行中…" : "更新归因"}
            </button>
          </div>
          <InlineActionFeedback
            labels={["盘后研究", "盘中复核", "经验归因"]}
            feedbackByAction={feedbackByAction}
            onOpenTaskCenter={onOpenTaskCenter}
          />
          {status?.last_run && (
            <div className={`inline-action-status ${String(status.last_run.status).toLowerCase()}`}>
              <strong>最近任务：{String(status.last_run.kind)}</strong>
              <span>{String(status.last_run.progress_message ?? status.last_run.blocker ?? status.last_run.status)}</span>
            </div>
          )}
        </article>

        <article className="panel">
          <PanelHead title="准入门禁" meta="NO SILENT FALLBACK" />
          <div className="gate-list">
            {(status?.blockers.length ? status.blockers : ["全部自检已通过"]).map(
              (item, index) => (
                <div
                  className={status?.blockers.length ? "gate blocked" : "gate passed"}
                  key={`${item}-${index}`}
                >
                  <span>{status?.blockers.length ? "×" : "✓"}</span>
                  {item}
                </div>
              ),
            )}
          </div>
        </article>

        <article className="panel span-2">
          <PanelHead title="最近研究" meta="THREE HORIZONS" />
          <ResearchTable rows={research.slice(0, 6)} />
        </article>

        <article className="panel">
          <PanelHead title="来源健康" meta="FREE PUBLIC DATA" />
          <div className="source-list">
            {(status?.source_health ?? []).map((source) => (
              <div key={source.source_id}>
                <span className={`pill ${source.status.toLowerCase()}`}>
                  {source.status}
                </span>
                <strong>{source.source_id}</strong>
                <small>{source.detail}</small>
              </div>
            ))}
          </div>
        </article>
      </div>
    </>
  );
}

export function ResearchView({ rows }: { rows: Research[] }) {
  const [selected, setSelected] = useState<Research | null>(rows[0] ?? null);
  useEffect(() => {
    if (!selected && rows.length) setSelected(rows[0]);
  }, [rows, selected]);

  return (
    <div className="split-view">
      <article className="panel list-panel">
        <PanelHead title="研究档案" meta={`${rows.length} DOSSIERS`} />
        {rows.map((row) => (
          <button
            className={selected?.id === row.id ? "research-card selected" : "research-card"}
            key={row.id}
            onClick={() => setSelected(row)}
          >
            <span className="mono">{row.symbol}</span>
            <strong>{row.name}</strong>
            <small>
              {row.synthesis?.verdict ?? "—"} · {row.trade_date}
            </small>
          </button>
        ))}
      </article>
      <article className="panel detail-panel">
        <PanelHead
          title={selected ? `${selected.symbol} ${selected.name}` : "选择研究档案"}
          meta="THESIS / OPPOSITION / SYNTHESIS"
        />
        {selected ? (
          <>
            <div className="callout">
              {selected.synthesis?.summary ?? "尚无综合结论"}
            </div>
            <div className="argument-grid">
              <div>
                <span className="eyebrow">THESIS</span>
                <strong>{selected.thesis?.summary ?? "未记录正方摘要"}</strong>
                <ul>
                  {(selected.thesis?.supporting_claims ?? []).map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
              <div>
                <span className="eyebrow">OPPOSITION</span>
                <strong>
                  {selected.opposition?.strongest_counter_thesis ?? "未记录反方摘要"}
                </strong>
                <ul>
                  {(selected.opposition?.evidence_gaps ?? []).map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            </div>
            <div className="horizon-grid">
              {(selected.synthesis?.horizon_views ?? []).map((view) => (
                <div className="horizon-card" key={view.horizon}>
                  <span className="eyebrow">{view.horizon}</span>
                  <strong>{view.action}</strong>
                  <div>目标 {percent(view.target_weight)}</div>
                  <div>确信 {percent(view.confidence)}</div>
                  <p>{view.rationale}</p>
                </div>
              ))}
            </div>
            <div className="evidence-count">
              引用证据 {selected.evidence_ids.length} 条 · 所有原始结论均不可覆盖
            </div>
          </>
        ) : (
          <Empty text="等待首份研究档案" />
        )}
      </article>
    </div>
  );
}

export function PortfolioView({
  portfolio,
  orders,
  fills,
}: {
  portfolio: Portfolio | null;
  orders: Order[];
  fills: Fill[];
}) {
  const total = Number(portfolio?.equity ?? 0) || 1;
  return (
    <div className="dashboard-grid">
      <article className="panel span-3">
        <PanelHead title="三周期资金视图" meta="SHORT 20 / SWING 40 / LONG 40 · ±10PP" />
        <div className="allocation-bars">
          {["SHORT", "SWING", "LONG"].map((horizon) => {
            const value = Number(portfolio?.horizon_values?.[horizon] ?? 0);
            return (
              <div key={horizon}>
                <div>
                  <strong>{horizon}</strong>
                  <span>{percent(value / total)}</span>
                </div>
                <i>
                  <b style={{ width: `${Math.min(100, (value / total) * 100)}%` }} />
                </i>
              </div>
            );
          })}
        </div>
      </article>
      <article className="panel span-3">
        <PanelHead title="分账持仓" meta="RISK MERGED / THESIS SEPARATED" />
        {portfolio?.positions.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>标的</th>
                  <th>周期</th>
                  <th>数量</th>
                  <th>成本</th>
                  <th>价格</th>
                  <th>市值</th>
                  <th>浮盈亏</th>
                </tr>
              </thead>
              <tbody>
                {portfolio.positions.map((row) => (
                  <tr key={`${row.instrument_id}-${row.horizon}`}>
                    <td>
                      <strong>{row.symbol}</strong>
                      <small>{row.name}</small>
                    </td>
                    <td>
                      <span className="pill neutral">{row.horizon}</span>
                    </td>
                    <td>{row.quantity}</td>
                    <td>{row.cost}</td>
                    <td>{row.price}</td>
                    <td>{money(row.market_value)}</td>
                    <td className={Number(row.unrealized_pnl) >= 0 ? "positive" : "negative"}>
                      {money(row.unrealized_pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty text="暂无持仓；现金是合法结果" />
        )}
      </article>
      <article className="panel span-2">
        <PanelHead title="订单计划" meta="PENDING / PARTIAL / BLOCKED" />
        {orders.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>标的</th><th>周期</th><th>方向</th><th>数量</th><th>状态</th></tr></thead>
              <tbody>
                {orders.map((row) => (
                  <tr key={row.id}>
                    <td><strong>{row.symbol}</strong><small>{row.name}</small></td>
                    <td>{row.horizon}</td><td>{row.side}</td>
                    <td>{row.filled_quantity}/{row.quantity}</td>
                    <td><span className="pill neutral">{row.status}</span><small>{row.blocked_reason}</small></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty text="暂无订单计划" />}
      </article>
      <article className="panel">
        <PanelHead title="模拟成交" meta="RULE VERSIONED" />
        {fills.length ? (
          <div className="fill-list">
            {fills.slice(0, 20).map((row) => (
              <div key={row.id}>
                <strong>{row.symbol} · {row.side} {row.quantity}</strong>
                <span>{row.fill_price} {row.currency}</span>
                <small>{row.local_trade_date} · 佣金 {row.commission} · 税费 {row.tax}</small>
              </div>
            ))}
          </div>
        ) : <Empty text="暂无模拟成交" />}
      </article>
    </div>
  );
}

export function MemoryView({
  experiences,
  lessons,
  onAction,
  busyActions,
  feedbackByAction,
  onOpenTaskCenter,
}: {
  experiences: Experience[];
  lessons: Lesson[];
  onAction: ActionRunner;
  busyActions: BusyActions;
  feedbackByAction: ActionFeedbackMap;
  onOpenTaskCenter: () => void;
}) {
  const [query, setQuery] = useState("");
  const [searchRows, setSearchRows] = useState<Experience[] | null>(null);
  const [feedback, setFeedback] = useState("");
  const visibleExperiences = searchRows ?? experiences;

  async function search(event: FormEvent) {
    event.preventDefault();
    const value = query.trim();
    if (!value) {
      setSearchRows(null);
      return;
    }
    setSearchRows(await api<Experience[]>(`/experiences/search?q=${encodeURIComponent(value)}`));
  }

  return (
    <div className="dashboard-grid">
      <article className="panel span-2">
        <PanelHead title="情景经验" meta="EPISODIC MEMORY / FTS5" />
        <form className="inline-form" onSubmit={search}>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="检索论点、标签或市场环境"
          />
          <button className="ghost">检索</button>
        </form>
        <div className="experience-list">
          {visibleExperiences.map((item) => (
            <div key={item.id}>
              <span className={`result-mark ${item.direction_hit ? "hit" : "miss"}`}>
                {item.direction_hit ? "HIT" : "MISS"}
              </span>
              <div>
                <strong>
                  {item.horizon} · {percent(item.excess_return)} 超额
                </strong>
                <p>{item.thesis_summary}</p>
                <small>
                  {item.outcome_date} · Brier {Number(item.brier_score).toFixed(3)}
                </small>
              </div>
            </div>
          ))}
          {!visibleExperiences.length && (
            <Empty text="决策到期归因后，经验会自动进入检索库" />
          )}
        </div>
      </article>
      <article className="panel">
        <PanelHead title="周度规律候选" meta="NOT ACTIVE RULES" />
        {lessons.map((item) => (
          <div className="lesson" key={item.id}>
            <span className="pill warning">{item.status}</span>
            <strong>{item.scope}</strong>
            <p>{item.hypothesis}</p>
            <small>确信 {percent(item.confidence)}</small>
          </div>
        ))}
        {!lessons.length && <Empty text="至少需要一批已归因经验" />}
        <button
          className="ghost wide"
          disabled={busyActions.has("周度总结")}
          onClick={() =>
            void onAction("周度总结", () =>
              api("/evolution/weekly", { method: "POST" }),
            )
          }
        >
          {busyActions.has("周度总结") ? "周度总结执行中…" : "生成周度规律"}
        </button>
        <InlineActionFeedback
          labels={["周度总结"]}
          feedbackByAction={feedbackByAction}
          onOpenTaskCenter={onOpenTaskCenter}
        />
        <div className="feedback-box">
          <label>
            用户反馈（独立经验源）
            <textarea
              value={feedback}
              onChange={(event) => setFeedback(event.target.value)}
              placeholder="记录你对研究、交易或风险解释的反馈"
            />
          </label>
          <button
            className="ghost wide"
            disabled={feedback.trim().length < 2 || busyActions.has("保存反馈")}
            onClick={() =>
              void onAction("保存反馈", async () => {
                await api("/feedback", {
                  method: "POST",
                  body: JSON.stringify({
                    target_type: "GENERAL",
                    content: feedback.trim(),
                    sentiment: "NEUTRAL",
                  }),
                });
                setFeedback("");
              })
            }
          >
            {busyActions.has("保存反馈") ? "保存中…" : "保存为经验来源"}
          </button>
          <InlineActionFeedback
            labels={["保存反馈"]}
            feedbackByAction={feedbackByAction}
            onOpenTaskCenter={onOpenTaskCenter}
          />
        </div>
      </article>
    </div>
  );
}

export function EvolutionView({
  rows,
  onAction,
  busyActions,
  feedbackByAction,
  onOpenTaskCenter,
}: {
  rows: Challenger[];
  onAction: ActionRunner;
  busyActions: BusyActions;
  feedbackByAction: ActionFeedbackMap;
  onOpenTaskCenter: () => void;
}) {
  return (
    <div className="dashboard-grid">
      <article className="panel span-3">
        <PanelHead
          title="冠军 / 挑战者"
          meta="REPLAY → 20 SHADOW DAYS → HUMAN APPROVAL"
        />
        <div className="button-row">
          <button
            className="primary"
            disabled={busyActions.has("月度挑战者")}
            onClick={() =>
              void onAction("月度挑战者", () =>
                api("/evolution/monthly", { method: "POST" }),
              )
            }
          >
            {busyActions.has("月度挑战者") ? "挑战者生成中…" : "生成月度挑战者"}
          </button>
          <button
            className="danger"
            disabled={busyActions.has("回滚冠军")}
            onClick={() =>
              void onAction("回滚冠军", () =>
                api(`/evolution/rollback?reason=${encodeURIComponent("用户从进化中心人工回滚")}`, {
                  method: "POST",
                }),
              )
            }
          >
            {busyActions.has("回滚冠军") ? "回滚中…" : "人工回滚冠军"}
          </button>
        </div>
        <InlineActionFeedback
          labels={["月度挑战者", "回滚冠军", "批准挑战者"]}
          feedbackByAction={feedbackByAction}
          onOpenTaskCenter={onOpenTaskCenter}
        />
        {rows.length ? (
          <div className="challenger-grid">
            {rows.map((row) => (
              <div className="challenger-card" key={row.id}>
                <div>
                  <span className={`pill ${row.status.toLowerCase()}`}>{row.status}</span>
                  <span className="mono">{row.id.slice(-8)}</span>
                </div>
                <h3>影子 {row.shadow_days}/20 日</h3>
                <p className="mono small">
                  {row.champion_version} → {row.strategy_version}
                </p>
                <dl>
                  <div><dt>回放案例</dt><dd>{row.replay_case_count}</dd></div>
                  <div><dt>挑战者超额</dt><dd>{percent(row.net_excess_return)}</dd></div>
                  <div><dt>冠军超额</dt><dd>{percent(row.champion_excess_return)}</dd></div>
                  <div><dt>最大回撤</dt><dd>{percent(row.max_drawdown)}</dd></div>
                </dl>
                <div className="difference-list">
                  <strong>规则差异</strong>
                  <span>
                    {row.differences.changed_rules.length
                      ? row.differences.changed_rules.join(" / ")
                      : "无顶层规则变化"}
                  </span>
                </div>
                {row.status === "ELIGIBLE" && (
                  <button
                    className="primary wide"
                    disabled={busyActions.has("批准挑战者")}
                    onClick={() =>
                      void onAction("批准挑战者", () =>
                        api(`/evolution/challengers/${row.id}/approve`, {
                          method: "POST",
                          body: JSON.stringify({
                            reason: "历史回放与20日影子门槛均通过",
                          }),
                        }),
                      )
                    }
                  >
                    {busyActions.has("批准挑战者") ? "批准中…" : "人工批准晋升"}
                  </button>
                )}
              </div>
            ))}
          </div>
        ) : (
          <Empty text="经验样本达到门槛后才能创建挑战者" />
        )}
      </article>
    </div>
  );
}

export function ChatView() {
  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState<Array<{
    role: "user" | "assistant";
    text: string;
  }>>([]);
  const [sending, setSending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const input = message.trim();
    if (!input || sending) return;
    setMessage("");
    setMessages((items) => [
      ...items,
      { role: "user", text: input },
      { role: "assistant", text: "" },
    ]);
    setSending(true);
    try {
      const response = await fetch(`${API_BASE}/api/v1/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: input }),
      });
      if (!response.ok || !response.body) throw new Error(await response.text());
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          const line = block.split("\n").find((item) => item.startsWith("data: "));
          if (!line) continue;
          const payload = JSON.parse(line.slice(6)) as { text?: string };
          if (payload.text) {
            setMessages((items) =>
              items.map((item, index) =>
                index === items.length - 1
                  ? { ...item, text: item.text + payload.text }
                  : item,
              ),
            );
          }
        }
      }
    } catch (error) {
      setMessages((items) =>
        items.map((item, index) =>
          index === items.length - 1
            ? { ...item, text: error instanceof Error ? error.message : "对话失败" }
            : item,
        ),
      );
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="chat-layout">
      <article className="panel chat-panel">
        <PanelHead title="与 Alpha Sage 对话" meta="EXPLAIN / QUESTION / RESEARCH TASK" />
        <div className="chat-stream">
          {messages.length ? (
            messages.map((item, index) => (
              <div className={`message ${item.role}`} key={index}>
                <span>{item.role === "user" ? "YOU" : "SAGE"}</span>
                <p>{item.text || "思考中…"}</p>
              </div>
            ))
          ) : (
            <Empty text="可以追问结论、要求解释交易或发起专题研究；对话不能绕过风控。" />
          )}
        </div>
        <form onSubmit={submit}>
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="例如：为什么中长线观点没有跟随今天的价格下跌改变？"
          />
          <button className="primary" disabled={sending}>发送</button>
        </form>
      </article>
    </div>
  );
}

export function SettingsView({
  sources,
  onAction,
  busyActions,
  latestModelTest,
  feedbackByAction,
  onOpenTaskCenter,
}: {
  sources: SourceHealth[];
  onAction: ActionRunner;
  busyActions: BusyActions;
  latestModelTest: AgentRun | null;
  feedbackByAction: ActionFeedbackMap;
  onOpenTaskCenter: () => void;
}) {
  const [form, setForm] = useState({
    base_url: "",
    api_mode: "responses",
    reasoning_model: "",
    fast_model: "",
    daily_request_budget: "",
    api_key: "",
  });
  const [savedForm, setSavedForm] = useState<typeof form | null>(null);
  const [apiKeyConfigured, setApiKeyConfigured] = useState(false);
  const [settingsError, setSettingsError] = useState("");

  useEffect(() => {
    void api<Record<string, unknown>>("/settings/model")
      .then((value) => {
        const loaded = {
          base_url: String(value.base_url ?? ""),
          api_mode: String(value.api_mode ?? "responses"),
          reasoning_model: String(value.reasoning_model ?? ""),
          fast_model: String(value.fast_model ?? ""),
          daily_request_budget: String(value.daily_request_budget ?? "100"),
          api_key: "",
        };
        setForm(loaded);
        setSavedForm(loaded);
        setApiKeyConfigured(Boolean(value.api_key_configured));
        setSettingsError("");
      })
      .catch((error) => setSettingsError(error instanceof Error ? error.message : "模型设置加载失败"));
  }, []);

  const comparable = (value: typeof form) => JSON.stringify({ ...value, api_key: "" });
  const dirty = Boolean(
    savedForm && (comparable(form) !== comparable(savedForm) || form.api_key.trim()),
  );
  const canTest =
    Boolean(form.base_url.trim() && form.reasoning_model.trim() && form.fast_model.trim()) &&
    (apiKeyConfigured || Boolean(form.api_key.trim()));
  const testChecks = Array.isArray(latestModelTest?.result?.checks)
    ? (latestModelTest.result.checks as Array<Record<string, unknown>>)
    : [];

  return (
    <div className="dashboard-grid">
      <article className="panel span-2">
        <PanelHead title="模型路由" meta="OPENAI COMPATIBLE / LOCAL SECRET" />
        {settingsError && <div className="inline-action-status failed">{settingsError}</div>}
        <div className="settings-state-row">
          <span className={`pill ${apiKeyConfigured ? "completed" : "failed"}`}>
            API Key {apiKeyConfigured ? "已配置" : "未配置"}
          </span>
          {dirty && <span className="pill warning">存在未保存修改</span>}
        </div>
        <div className="form-grid">
          <label>
            Base URL
            <input
              value={form.base_url}
              onChange={(event) => setForm({ ...form, base_url: event.target.value })}
            />
          </label>
          <label>
            每日模型调用预算
            <input
              type="number"
              min="1"
              max="1000"
              value={form.daily_request_budget}
              onChange={(event) =>
                setForm({ ...form, daily_request_budget: event.target.value })
              }
            />
          </label>
          <label>
            API 模式
            <select
              value={form.api_mode}
              onChange={(event) => setForm({ ...form, api_mode: event.target.value })}
            >
              <option value="responses">Responses</option>
              <option value="chat_completions">Chat Completions</option>
            </select>
          </label>
          <label>
            推理模型
            <input
              value={form.reasoning_model}
              onChange={(event) =>
                setForm({ ...form, reasoning_model: event.target.value })
              }
            />
          </label>
          <label>
            快速模型
            <input
              value={form.fast_model}
              onChange={(event) => setForm({ ...form, fast_model: event.target.value })}
            />
          </label>
          <label className="span-2">
            API Key（保存到系统密钥环）
            <input
              type="password"
              value={form.api_key}
              onChange={(event) => setForm({ ...form, api_key: event.target.value })}
              placeholder={apiKeyConfigured ? "已配置；留空表示继续使用现有密钥" : "请输入 API Key"}
            />
          </label>
        </div>
        <div className="button-row">
          <button
            className="primary"
            disabled={busyActions.has("保存模型设置") || !dirty}
            onClick={() =>
              void onAction("保存模型设置", async () => {
                const value = await api<Record<string, unknown>>("/settings/model", {
                  method: "PUT",
                  body: JSON.stringify(form),
                });
                const saved = { ...form, api_key: "" };
                setForm(saved);
                setSavedForm(saved);
                setApiKeyConfigured(Boolean(value.api_key_configured));
                return value;
              })
            }
          >
            {busyActions.has("保存模型设置") ? "保存中…" : "保存设置"}
          </button>
          <button
            className="ghost"
            disabled={!canTest || busyActions.has("测试模型连接")}
            onClick={() =>
              void onAction("测试模型连接", () =>
                api("/settings/model/test", {
                  method: "POST",
                  body: JSON.stringify({
                    ...form,
                    daily_request_budget: Number(form.daily_request_budget),
                  }),
                }),
              )
            }
          >
            {busyActions.has("测试模型连接") ? "正在测试两个模型…" : "测试连接"}
          </button>
        </div>
        <InlineActionFeedback
          labels={["保存模型设置", "测试模型连接"]}
          feedbackByAction={feedbackByAction}
          onOpenTaskCenter={onOpenTaskCenter}
        />
        {!canTest && (
          <p className="muted">测试需要完整的 Base URL、两个模型名，以及已保存或当前输入的 API Key。</p>
        )}
        {latestModelTest && (
          <div className="model-test-results">
            <div className="inline-action-status">
              <strong>最近模型测试：{latestModelTest.status}</strong>
              <span>{latestModelTest.progress_message ?? latestModelTest.blocker ?? "等待结果"}</span>
            </div>
            {testChecks.map((check) => (
              <div className={`model-test-card ${String(check.status).toLowerCase()}`} key={String(check.role)}>
                <div>
                  <span className={`pill ${String(check.status).toLowerCase()}`}>{String(check.status)}</span>
                  <strong>{check.role === "reasoning" ? "推理模型" : "快速模型"}</strong>
                </div>
                <p>{String(check.model ?? "")}</p>
                <small>{String(check.message ?? "")}</small>
                <small className="model-test-meta">
                  {String(check.endpoint ?? "")}
                  {check.http_status ? ` · HTTP ${String(check.http_status)}` : ""}
                  {check.error_type ? ` · ${String(check.error_type)}` : ""}
                  {` · ${String(check.latency_ms ?? 0)}ms`}
                </small>
                {Boolean(check.request_id) && (
                  <small className="model-test-meta">Request ID: {String(check.request_id)}</small>
                )}
              </div>
            ))}
          </div>
        )}
      </article>
      <article className="panel">
        <PanelHead title="数据初始化" meta="5 YEARS / DUAL SOURCE" />
        <p className="muted">
          首次同步会读取免费公开来源并按标的封存 Parquet。全市场任务可能运行较久，可先验证少量标的。
        </p>
        <div className="button-stack">
          <button
            className="primary wide"
            disabled={busyActions.has("五年历史同步")}
            onClick={() =>
              void onAction("五年历史同步", () =>
                api("/data/sync-history?years=5", { method: "POST" }),
              )
            }
          >
            {busyActions.has("五年历史同步") ? "历史同步执行中…" : "同步完整五年历史"}
          </button>
          <button
            className="ghost wide"
            disabled={busyActions.has("小规模数据验证")}
            onClick={() =>
              void onAction("小规模数据验证", () =>
                api("/data/sync-history?years=5&limit=10", { method: "POST" }),
              )
            }
          >
            {busyActions.has("小规模数据验证") ? "数据验证执行中…" : "先验证 10 个标的"}
          </button>
        </div>
        <InlineActionFeedback
          labels={["五年历史同步", "小规模数据验证"]}
          feedbackByAction={feedbackByAction}
          onOpenTaskCenter={onOpenTaskCenter}
        />
      </article>
      <article className="panel span-3">
        <PanelHead title="来源注册表" meta="NO SLA / EXPLICIT HEALTH" />
        <div className="source-grid">
          {sources.map((source) => (
            <div key={source.source_id}>
              <span className={`pill ${source.status.toLowerCase()}`}>
                {source.status}
              </span>
              <strong>{source.source_id}</strong>
              <small>{source.role}</small>
              <p>{source.detail}</p>
            </div>
          ))}
        </div>
      </article>
    </div>
  );
}

function InlineActionFeedback({
  labels,
  feedbackByAction,
  onOpenTaskCenter,
}: {
  labels: string[];
  feedbackByAction: ActionFeedbackMap;
  onOpenTaskCenter: () => void;
}) {
  const items = labels
    .map((label) => feedbackByAction.get(label))
    .filter((item): item is ActionFeedbackSummary => Boolean(item));
  if (!items.length) return null;
  return (
    <div className="inline-action-feedback-list">
      {items.map((item) => (
        <button
          className={`inline-action-status ${item.status.toLowerCase()}`}
          key={item.id}
          onClick={onOpenTaskCenter}
        >
          <strong>{item.label} · {item.status}</strong>
          <span>{item.stage ? `${item.stage} · ` : ""}{item.message}</span>
          <small>打开任务详情</small>
        </button>
      ))}
    </div>
  );
}

function Metric({
  label,
  value,
  meta,
  tone = "normal",
}: {
  label: string;
  value: string;
  meta: string;
  tone?: "normal" | "risk";
}) {
  return (
    <div className={`metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{meta}</small>
    </div>
  );
}

function PanelHead({ title, meta }: { title: string; meta: string }) {
  return (
    <div className="panel-head">
      <h2>{title}</h2>
      <span>{meta}</span>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="empty">
      <span>◇</span>
      {text}
    </div>
  );
}

function ResearchTable({ rows }: { rows: Research[] }) {
  return rows.length ? (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>标的</th><th>结论</th><th>短线 / 波段 / 长线</th><th>证据</th><th>日期</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td><strong>{row.symbol}</strong><small>{row.name}</small></td>
              <td>
                <span className={`pill ${(row.synthesis?.verdict ?? "watch").toLowerCase()}`}>
                  {row.synthesis?.verdict ?? "WATCH"}
                </span>
              </td>
              <td>
                {(row.synthesis?.horizon_views ?? []).map((view) => (
                  <span className="mini-action" key={view.horizon}>
                    {view.horizon.slice(0, 1)}:{view.action}
                  </span>
                ))}
              </td>
              <td>{row.evidence_ids.length}</td>
              <td className="mono">{row.trade_date}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty text="数据、模型与账户启用后才会生成真实研究" />
  );
}
