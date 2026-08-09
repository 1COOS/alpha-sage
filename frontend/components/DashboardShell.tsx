"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import {
  ChatView,
  EvolutionView,
  MemoryView,
  PortfolioView,
  ResearchView,
  SettingsView,
  TodayView,
} from "./views";
import type {
  AgentRun,
  ActionFeedbackMap,
  ActionFeedbackSummary,
  BusyActions,
  Challenger,
  Experience,
  Fill,
  Lesson,
  LocalActionFeedback,
  Order,
  Portfolio,
  Research,
  RunAccepted,
  SystemStatus,
  View,
} from "./types";

const NAV: Array<{ id: View; label: string; code: string }> = [
  { id: "today", label: "今日驾驶舱", code: "01" },
  { id: "research", label: "研究中心", code: "02" },
  { id: "portfolio", label: "组合中心", code: "03" },
  { id: "memory", label: "经验中心", code: "04" },
  { id: "evolution", label: "进化中心", code: "05" },
  { id: "chat", label: "对话", code: "06" },
  { id: "settings", label: "设置", code: "07" },
];

const ACTIVE_STATUSES = new Set(["PENDING", "RUNNING"]);
const FAILURE_STATUSES = new Set(["FAILED", "BLOCKED"]);
const LOCAL_ACTIONS_KEY = "alpha-sage-action-feedback-v1";
const DISMISSED_TASKS_KEY = "alpha-sage-dismissed-tasks-v1";

const RUN_LABELS: Record<string, string> = {
  EOD: "盘后研究",
  INTRADAY: "盘中复核",
  ATTRIBUTION: "经验归因",
  WEEKLY: "周度总结",
  MONTHLY: "月度挑战者",
  DATA_SYNC: "历史同步",
  MODEL_TEST: "测试模型连接",
};

function isRunAccepted(value: unknown): value is RunAccepted {
  return Boolean(
    value &&
      typeof value === "object" &&
      typeof (value as Record<string, unknown>).run_id === "string",
  );
}

function summarizeResult(label: string, result: unknown): string {
  if (!result || typeof result !== "object") return `${label}完成`;
  const value = result as Record<string, unknown>;
  if (label === "启用账户") return "模拟账户已通过自检并启用";
  if (label === "暂停账户") return String(value.reason ?? "模拟账户已暂停");
  if (label === "保存模型设置") return "模型设置已保存；API Key 不会回显";
  if (label === "保存反馈") return "反馈已保存为独立经验来源";
  if (label === "批准挑战者") return "挑战者已人工批准晋升";
  if (label === "回滚冠军") return "冠军策略已人工回滚";
  if (label === "刷新") return "页面数据已刷新";
  return `${label}完成`;
}

function businessFailure(result: unknown): string | null {
  if (!result || typeof result !== "object") return null;
  const value = result as Record<string, unknown>;
  if (!FAILURE_STATUSES.has(String(value.status ?? ""))) return null;
  return String(value.progress_message ?? value.blocker ?? value.message ?? `${value.status}`);
}

function labelForRun(run: AgentRun): string | null {
  if (run.kind === "DATA_SYNC") {
    if (run.parameters.action_label) return String(run.parameters.action_label);
    return run.parameters.limit ? "小规模数据验证" : "五年历史同步";
  }
  return RUN_LABELS[run.kind] ?? null;
}

function formatElapsed(startedAt: string, finishedAt: string | null | undefined, now: number): string {
  const started = Date.parse(startedAt);
  const ended = finishedAt ? Date.parse(finishedAt) : now;
  if (!Number.isFinite(started) || !Number.isFinite(ended)) return "耗时未知";
  const seconds = Math.max(0, Math.floor((ended - started) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} 分 ${seconds % 60} 秒`;
}

function safeParse<T>(raw: string | null, fallback: T): T {
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function DashboardShell() {
  const [view, setView] = useState<View>("today");
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [research, setResearch] = useState<Research[]>([]);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [orders, setOrders] = useState<Order[]>([]);
  const [fills, setFills] = useState<Fill[]>([]);
  const [experiences, setExperiences] = useState<Experience[]>([]);
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [challengers, setChallengers] = useState<Challenger[]>([]);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [submitting, setSubmitting] = useState<Set<string>>(new Set());
  const [localActions, setLocalActions] = useState<LocalActionFeedback[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [taskCenterOpen, setTaskCenterOpen] = useState(false);
  const [clock, setClock] = useState(() => Date.now());

  useEffect(() => {
    setLocalActions(safeParse(window.localStorage.getItem(LOCAL_ACTIONS_KEY), []));
    setDismissed(new Set(safeParse<string[]>(window.localStorage.getItem(DISMISSED_TASKS_KEY), [])));
  }, []);

  useEffect(() => {
    window.localStorage.setItem(LOCAL_ACTIONS_KEY, JSON.stringify(localActions.slice(0, 50)));
  }, [localActions]);

  useEffect(() => {
    window.localStorage.setItem(DISMISSED_TASKS_KEY, JSON.stringify([...dismissed]));
  }, [dismissed]);

  const loadAll = useCallback(async () => {
    const [
      nextStatus,
      nextPortfolio,
      nextOrders,
      nextFills,
      nextResearch,
      nextExperiences,
      nextLessons,
      nextChallengers,
      nextRuns,
    ] = await Promise.all([
      api<SystemStatus>("/system/status"),
      api<Portfolio>("/portfolio"),
      api<Order[]>("/orders?limit=100"),
      api<Fill[]>("/fills?limit=100"),
      api<Research[]>("/research?limit=40"),
      api<Experience[]>("/experiences?limit=80"),
      api<Lesson[]>("/lessons"),
      api<Challenger[]>("/evolution/challengers"),
      api<AgentRun[]>("/agent/runs?limit=50"),
    ]);
    setStatus(nextStatus);
    setPortfolio(nextPortfolio);
    setOrders(nextOrders);
    setFills(nextFills);
    setResearch(nextResearch);
    setExperiences(nextExperiences);
    setLessons(nextLessons);
    setChallengers(nextChallengers);
    setRuns(nextRuns);
  }, []);

  const recordLoadFailure = useCallback((error: unknown) => {
    const now = new Date().toISOString();
    setLocalActions((items) => [
      {
        id: "local-refresh-error",
        label: "刷新",
        status: "FAILED",
        message: error instanceof Error ? error.message : "无法连接本地服务",
        started_at: now,
        finished_at: now,
      },
      ...items.filter((item) => item.id !== "local-refresh-error"),
    ]);
  }, []);

  useEffect(() => {
    void loadAll().catch(recordLoadFailure);
    const timer = window.setInterval(() => void loadAll().catch(recordLoadFailure), 30_000);
    return () => window.clearInterval(timer);
  }, [loadAll, recordLoadFailure]);

  const activeRuns = runs.filter((run) => ACTIVE_STATUSES.has(run.status));
  const hasRunningLocalAction = localActions.some((item) => item.status === "RUNNING");
  useEffect(() => {
    if (!taskCenterOpen && !activeRuns.length && !hasRunningLocalAction) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [activeRuns.length, hasRunningLocalAction, taskCenterOpen]);

  useEffect(() => {
    if (!activeRuns.length) return;
    const timer = window.setInterval(() => {
      void api<AgentRun[]>("/agent/runs?limit=50")
        .then((nextRuns) => {
          setRuns(nextRuns);
          if (!nextRuns.some((run) => ACTIVE_STATUSES.has(run.status))) {
            void loadAll().catch(recordLoadFailure);
          }
        })
        .catch(recordLoadFailure);
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [activeRuns.length, loadAll, recordLoadFailure]);

  async function action(label: string, operation: () => Promise<unknown>) {
    const localId = `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const startedAt = new Date().toISOString();
    setSubmitting((items) => new Set(items).add(label));
    setLocalActions((items) => [
      { id: localId, label, status: "RUNNING", message: `正在执行：${label}`, started_at: startedAt },
      ...items,
    ]);
    try {
      const result = await operation();
      const failure = businessFailure(result);
      if (failure && !isRunAccepted(result)) throw new Error(failure);
      if (isRunAccepted(result)) {
        setLocalActions((items) => items.filter((item) => item.id !== localId));
        setRuns((items) => [
          {
            id: result.run_id,
            kind: result.kind,
            status: result.status,
            trigger_source: "MANUAL",
            parameters: { action_label: label },
            stage: result.stage,
            progress_message: result.message,
            result: {},
            started_at: startedAt,
          },
          ...items.filter((item) => item.id !== result.run_id),
        ]);
        setTaskCenterOpen(true);
      } else {
        const finishedAt = new Date().toISOString();
        setLocalActions((items) =>
          items.map((item) =>
            item.id === localId
              ? {
                  ...item,
                  status: "COMPLETED",
                  message: summarizeResult(label, result),
                  detail: JSON.stringify(result ?? {}),
                  finished_at: finishedAt,
                }
              : item,
          ),
        );
        void loadAll().catch(recordLoadFailure);
      }
    } catch (error) {
      const finishedAt = new Date().toISOString();
      setLocalActions((items) =>
        items.map((item) =>
          item.id === localId
            ? {
                ...item,
                status: "FAILED",
                message: error instanceof Error ? error.message : `${label}失败`,
                finished_at: finishedAt,
              }
            : item,
        ),
      );
      setTaskCenterOpen(true);
    } finally {
      setSubmitting((items) => {
        const next = new Set(items);
        next.delete(label);
        return next;
      });
    }
  }

  const busyActions: BusyActions = useMemo(() => {
    const result = new Set(submitting);
    for (const run of activeRuns) {
      const label = RUN_LABELS[run.kind];
      if (label) result.add(label);
      if (run.kind === "DATA_SYNC") {
        result.add("五年历史同步");
        result.add("小规模数据验证");
      }
    }
    return result;
  }, [activeRuns, submitting]);

  const latestModelTest = runs.find((run) => run.kind === "MODEL_TEST") ?? null;
  const feedbackByAction: ActionFeedbackMap = useMemo(() => {
    const candidates: Array<ActionFeedbackSummary & { started_at: string }> = [
      ...localActions.filter((item) => !dismissed.has(item.id)).map((item) => ({
        id: item.id,
        label: item.label,
        status: item.status,
        message: item.message,
        started_at: item.started_at,
      })),
      ...runs.filter((run) => !dismissed.has(run.id)).flatMap((run) => {
        const label = labelForRun(run);
        if (!label) return [];
        return [{
          id: run.id,
          label,
          status: run.status,
          message: run.progress_message ?? run.blocker ?? run.status,
          stage: run.stage,
          started_at: run.started_at,
        }];
      }),
    ].sort((left, right) => right.started_at.localeCompare(left.started_at));
    const result = new Map<string, ActionFeedbackSummary>();
    for (const item of candidates) {
      if (!result.has(item.label)) result.set(item.label, item);
    }
    return result;
  }, [dismissed, localActions, runs]);
  const visibleRuns = runs.filter((run) => !dismissed.has(run.id));
  const visibleLocal = localActions.filter((item) => !dismissed.has(item.id));
  const activeCount = activeRuns.length + localActions.filter((item) => item.status === "RUNNING").length;
  const failedCount =
    visibleRuns.filter((run) => run.status === "FAILED" || run.status === "BLOCKED").length +
    visibleLocal.filter((item) => item.status === "FAILED").length;

  function dismissTask(id: string) {
    setDismissed((items) => new Set(items).add(id));
  }

  function clearFinished() {
    setDismissed((items) => {
      const next = new Set(items);
      for (const run of runs) if (!ACTIVE_STATUSES.has(run.status)) next.add(run.id);
      for (const item of localActions) if (item.status !== "RUNNING") next.add(item.id);
      return next;
    });
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">AS</div>
          <div>
            <strong>ALPHA SAGE</strong>
            <span>COGNITIVE INVESTING</span>
          </div>
        </div>
        <nav>
          {NAV.map((item) => (
            <button
              className={view === item.id ? "nav-item active" : "nav-item"}
              key={item.id}
              onClick={() => setView(item.id)}
            >
              <span>{item.code}</span>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="system-mini">
          <div className="eyebrow">SYSTEM STATE</div>
          <div className="status-line">
            <i className={status?.account_enabled ? "dot live" : "dot"} />
            {status?.account_enabled ? "模拟账户运行中" : "模拟账户已暂停"}
          </div>
          <div className="mono small">{status?.current_strategy ?? "连接中…"}</div>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <div className="eyebrow">LOCAL / CN MARKET / PAPER</div>
            <h1>{NAV.find((item) => item.id === view)?.label}</h1>
          </div>
          <div className="top-actions">
            <button className="ghost task-center-trigger" onClick={() => setTaskCenterOpen(true)}>
              任务中心 {activeCount ? `· ${activeCount} 运行中` : ""}
              {failedCount ? <b>{failedCount}</b> : null}
            </button>
            <button
              className="ghost"
              disabled={busyActions.has("刷新")}
              onClick={() => void action("刷新", loadAll)}
            >
              {busyActions.has("刷新") ? "刷新中…" : "刷新"}
            </button>
            {status?.account_enabled ? (
              <button
                className="danger"
                disabled={busyActions.has("暂停账户")}
                onClick={() =>
                  void action("暂停账户", () => api("/system/pause", { method: "POST" }))
                }
              >
                {busyActions.has("暂停账户") ? "暂停中…" : "暂停账户"}
              </button>
            ) : (
              <button
                className="primary"
                disabled={busyActions.has("启用账户")}
                onClick={() =>
                  void action("启用账户", () =>
                    api("/system/enable", {
                      method: "POST",
                      body: JSON.stringify({ confirmation: "ENABLE PAPER ACCOUNT" }),
                    }),
                  )
                }
              >
                {busyActions.has("启用账户") ? "自检中…" : "自检并启用"}
              </button>
            )}
          </div>
        </header>

        <ActionFeedbackRow
          labels={["刷新", "启用账户", "暂停账户"]}
          feedbackByAction={feedbackByAction}
          onOpen={() => setTaskCenterOpen(true)}
        />

        <section className="content">
          {view === "today" && (
            <TodayView
              status={status}
              portfolio={portfolio}
              research={research}
              onAction={action}
              busyActions={busyActions}
              feedbackByAction={feedbackByAction}
              onOpenTaskCenter={() => setTaskCenterOpen(true)}
            />
          )}
          {view === "research" && <ResearchView rows={research} />}
          {view === "portfolio" && (
            <PortfolioView portfolio={portfolio} orders={orders} fills={fills} />
          )}
          {view === "memory" && (
            <MemoryView
              experiences={experiences}
              lessons={lessons}
              onAction={action}
              busyActions={busyActions}
              feedbackByAction={feedbackByAction}
              onOpenTaskCenter={() => setTaskCenterOpen(true)}
            />
          )}
          {view === "evolution" && (
            <EvolutionView
              rows={challengers}
              onAction={action}
              busyActions={busyActions}
              feedbackByAction={feedbackByAction}
              onOpenTaskCenter={() => setTaskCenterOpen(true)}
            />
          )}
          {view === "chat" && <ChatView />}
          {view === "settings" && (
            <SettingsView
              sources={status?.source_health ?? []}
              onAction={action}
              busyActions={busyActions}
              latestModelTest={latestModelTest}
              feedbackByAction={feedbackByAction}
              onOpenTaskCenter={() => setTaskCenterOpen(true)}
            />
          )}
        </section>
      </main>

      {taskCenterOpen && (
        <div className="task-center-backdrop" onClick={() => setTaskCenterOpen(false)}>
          <aside className="task-center" onClick={(event) => event.stopPropagation()}>
            <div className="task-center-head">
              <div>
                <div className="eyebrow">PERSISTENT FEEDBACK</div>
                <h2>任务中心</h2>
              </div>
              <div>
                <button className="ghost" onClick={clearFinished}>清除已完成</button>
                <button className="ghost" onClick={() => setTaskCenterOpen(false)}>关闭</button>
              </div>
            </div>
            <div className="task-list">
              {[...visibleLocal, ...visibleRuns]
                .sort((left, right) =>
                  String("started_at" in right ? right.started_at : "").localeCompare(
                    String("started_at" in left ? left.started_at : ""),
                  ),
                )
                .map((item) =>
                  "kind" in item ? (
                    <RunTaskCard
                      key={item.id}
                      run={item}
                      now={clock}
                      onDismiss={dismissTask}
                    />
                  ) : (
                    <LocalTaskCard
                      key={item.id}
                      item={item}
                      now={clock}
                      onDismiss={dismissTask}
                    />
                  ),
                )}
              {!visibleLocal.length && !visibleRuns.length && (
                <div className="empty"><span>✓</span><p>暂无未关闭的任务反馈</p></div>
              )}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}

function ActionFeedbackRow({
  labels,
  feedbackByAction,
  onOpen,
}: {
  labels: string[];
  feedbackByAction: ActionFeedbackMap;
  onOpen: () => void;
}) {
  const items = labels
    .map((label) => feedbackByAction.get(label))
    .filter((item): item is ActionFeedbackSummary => Boolean(item));
  if (!items.length) return null;
  return (
    <div className="top-action-feedback">
      {items.map((item) => (
        <button key={item.id} onClick={onOpen}>
          <span className={`pill ${item.status.toLowerCase()}`}>{item.status}</span>
          <strong>{item.label}</strong>
          <small>{item.stage ? `${item.stage} · ` : ""}{item.message}</small>
        </button>
      ))}
    </div>
  );
}

function RunTaskCard({
  run,
  now,
  onDismiss,
}: {
  run: AgentRun;
  now: number;
  onDismiss: (id: string) => void;
}) {
  const active = ACTIVE_STATUSES.has(run.status);
  const progress =
    run.progress_total && run.progress_current != null
      ? Math.min(100, (run.progress_current / run.progress_total) * 100)
      : null;
  const sourceLabel =
    run.trigger_source === "SCHEDULER"
      ? "自动调度"
      : run.trigger_source === "MANUAL"
        ? "手动触发"
        : "系统触发";
  const message =
    run.progress_message ||
    run.blocker ||
    (active ? "等待状态更新" : run.status === "COMPLETED" ? "任务已完成，结果已保留" : run.status);
  return (
    <article className={`task-card ${run.status.toLowerCase()}`}>
      <div className="task-card-head">
        <div>
          <span className={`pill ${run.status.toLowerCase()}`}>{run.status}</span>
          <strong>{RUN_LABELS[run.kind] ?? run.kind}</strong>
        </div>
        {!active && <button className="mini-button" onClick={() => onDismiss(run.id)}>关闭</button>}
      </div>
      <p>{message}</p>
      {progress != null && (
        <div className="task-progress"><i style={{ width: `${progress}%` }} /></div>
      )}
      <small>
        {sourceLabel}
        {run.stage ? ` · ${run.stage}` : ""}
        {run.progress_total ? ` · ${run.progress_current ?? 0}/${run.progress_total}` : ""}
        {` · ${formatElapsed(run.started_at, run.finished_at, now)}`}
      </small>
      {Object.keys(run.result ?? {}).length > 0 && (
        <details><summary>查看结果</summary><pre>{JSON.stringify(run.result, null, 2)}</pre></details>
      )}
    </article>
  );
}

function LocalTaskCard({
  item,
  now,
  onDismiss,
}: {
  item: LocalActionFeedback;
  now: number;
  onDismiss: (id: string) => void;
}) {
  return (
    <article className={`task-card ${item.status.toLowerCase()}`}>
      <div className="task-card-head">
        <div>
          <span className={`pill ${item.status.toLowerCase()}`}>{item.status}</span>
          <strong>{item.label}</strong>
        </div>
        {item.status !== "RUNNING" && (
          <button className="mini-button" onClick={() => onDismiss(item.id)}>关闭</button>
        )}
      </div>
      <p>{item.message}</p>
      <small>页面动作 · {formatElapsed(item.started_at, item.finished_at, now)}</small>
    </article>
  );
}
