"use client";

import { useCallback, useEffect, useState } from "react";
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
  Challenger,
  Experience,
  Fill,
  Lesson,
  Order,
  Portfolio,
  Research,
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
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string>("");

  const refresh = useCallback(async () => {
    try {
      const [
        nextStatus,
        nextPortfolio,
        nextOrders,
        nextFills,
        nextResearch,
        nextExperiences,
        nextLessons,
        nextChallengers,
      ] = await Promise.all([
        api<SystemStatus>("/system/status"),
        api<Portfolio>("/portfolio"),
        api<Order[]>("/orders?limit=100"),
        api<Fill[]>("/fills?limit=100"),
        api<Research[]>("/research?limit=40"),
        api<Experience[]>("/experiences?limit=80"),
        api<Lesson[]>("/lessons"),
        api<Challenger[]>("/evolution/challengers"),
      ]);
      setStatus(nextStatus);
      setPortfolio(nextPortfolio);
      setOrders(nextOrders);
      setFills(nextFills);
      setResearch(nextResearch);
      setExperiences(nextExperiences);
      setLessons(nextLessons);
      setChallengers(nextChallengers);
      setNotice("");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法连接本地服务");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function action(label: string, operation: () => Promise<unknown>) {
    setBusy(label);
    setNotice("");
    try {
      await operation();
      setNotice(`${label}已提交`);
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : `${label}失败`);
    } finally {
      setBusy(null);
    }
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
            <button className="ghost" onClick={() => void refresh()}>
              刷新
            </button>
            {status?.account_enabled ? (
              <button
                className="danger"
                onClick={() =>
                  void action("暂停账户", () =>
                    api("/system/pause", { method: "POST" }),
                  )
                }
              >
                暂停账户
              </button>
            ) : (
              <button
                className="primary"
                onClick={() =>
                  void action("启用账户", () =>
                    api("/system/enable", {
                      method: "POST",
                      body: JSON.stringify({ confirmation: "ENABLE PAPER ACCOUNT" }),
                    }),
                  )
                }
              >
                自检并启用
              </button>
            )}
          </div>
        </header>

        {notice && <div className="notice">{notice}</div>}
        {busy && (
          <div className="progress">
            <span /> 正在执行：{busy}
          </div>
        )}

        <section className="content">
          {view === "today" && (
            <TodayView
              status={status}
              portfolio={portfolio}
              research={research}
              onAction={action}
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
            />
          )}
          {view === "evolution" && (
            <EvolutionView rows={challengers} onAction={action} />
          )}
          {view === "chat" && <ChatView />}
          {view === "settings" && (
            <SettingsView
              sources={status?.source_health ?? []}
              onAction={action}
            />
          )}
        </section>
      </main>
    </div>
  );
}
