import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardShell } from "./DashboardShell";

function response(payload: unknown, ok = true) {
  return {
    ok,
    status: ok ? 200 : 500,
    statusText: ok ? "OK" : "ERROR",
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  };
}

function basePayload(url: string) {
  if (url.includes("/system/status")) {
    return {
      account_enabled: false,
      account_cash: "1000000",
      equity: "1000000",
      drawdown: "0",
      current_strategy: "alpha-sage-cognition-v1",
      last_run: null,
      source_health: [],
      blockers: ["尚未完成初始化自检"],
    };
  }
  if (url.endsWith("/portfolio")) {
    return {
      account: { enabled: false, cash: "1000000" },
      positions: [],
      cash: "1000000",
      market_value: "0",
      equity: "1000000",
      drawdown: "0",
      risk_state: "NORMAL",
      horizon_values: {},
    };
  }
  if (url.includes("/settings/model")) {
    return {
      base_url: "https://provider.example/v1",
      api_mode: "responses",
      reasoning_model: "reasoning-model",
      fast_model: "fast-model",
      daily_request_budget: 100,
      api_key_configured: true,
      sources: {},
    };
  }
  return [];
}

describe("DashboardShell", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/settings/model/test") && init?.method === "POST") {
          return response({
            run_id: "run_model_test",
            kind: "MODEL_TEST",
            status: "PENDING",
            stage: "QUEUED",
            message: "等待前序任务完成",
          });
        }
        return response(basePayload(url));
      }),
    );
  });

  it("renders the local investment workbench navigation and task center", async () => {
    render(<DashboardShell />);
    expect(screen.getByText("ALPHA SAGE")).toBeInTheDocument();
    expect(screen.getAllByText("今日驾驶舱").length).toBeGreaterThan(0);
    expect(screen.getByText("进化中心")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /任务中心/ }));
    expect(await screen.findByRole("heading", { name: "任务中心" })).toBeInTheDocument();
  });

  it("shows configured API key and tests the current unsaved model form", async () => {
    render(<DashboardShell />);
    fireEvent.click(screen.getByRole("button", { name: /设置/ }));

    expect(await screen.findByText("API Key 已配置")).toBeInTheDocument();
    const reasoning = screen.getByLabelText("推理模型");
    fireEvent.change(reasoning, { target: { value: "reasoning-unsaved" } });
    expect(screen.getByText("存在未保存修改")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
    await waitFor(() => {
      const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls;
      const call = calls.find(([url]) => String(url).endsWith("/settings/model/test"));
      expect(call).toBeTruthy();
      const body = JSON.parse(String(call?.[1]?.body));
      expect(body.reasoning_model).toBe("reasoning-unsaved");
    });
    expect(await screen.findByText(/1 运行中/)).toBeInTheDocument();
  });

  it("keeps failed action feedback until the user closes it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/system/status")) {
          return response({ detail: "后端不可用" }, false);
        }
        return response(basePayload(url));
      }),
    );
    render(<DashboardShell />);
    fireEvent.click(screen.getByRole("button", { name: /任务中心/ }));
    expect((await screen.findAllByText("后端不可用")).length).toBeGreaterThan(0);
    const closeButtons = screen.getAllByRole("button", { name: "关闭" });
    fireEvent.click(closeButtons[closeButtons.length - 1]);
    await waitFor(() => expect(screen.queryByText("后端不可用")).not.toBeInTheDocument());
    await waitFor(() =>
      expect(window.localStorage.getItem("alpha-sage-dismissed-tasks-v1")).toContain(
        "local-refresh-error",
      ),
    );
  });

  it("restores an active backend task after refresh and polls it to completion", async () => {
    vi.useFakeTimers();
    let runReads = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/agent/runs")) {
          runReads += 1;
          return response([
            {
              id: "run_eod_restored",
              kind: "EOD",
              status: runReads === 1 ? "RUNNING" : "COMPLETED",
              trigger_source: "MANUAL",
              parameters: {},
              stage: runReads === 1 ? "RESEARCH_THESIS" : "COMPLETED",
              progress_message: runReads === 1 ? "600000 浦发银行：生成正方研究" : "盘后研究已完成",
              progress_current: runReads === 1 ? 1 : 4,
              progress_total: 4,
              result: runReads === 1 ? {} : { researched_count: 1 },
              started_at: "2026-08-07T08:00:00Z",
              finished_at: runReads === 1 ? null : "2026-08-07T08:00:02Z",
            },
          ]);
        }
        return response(basePayload(url));
      }),
    );

    render(<DashboardShell />);
    await act(async () => Promise.resolve());
    expect(screen.getByRole("button", { name: "盘后研究执行中…" })).toBeDisabled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(screen.getByText(/盘后研究已完成/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "运行盘后研究" })).toBeEnabled();
  });

  it("renders independent reasoning and fast model test result cards", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/agent/runs")) {
          return response([
            {
              id: "run_model_completed",
              kind: "MODEL_TEST",
              status: "FAILED",
              trigger_source: "MANUAL",
              parameters: {},
              stage: "TEST_FAST",
              progress_message: "模型连接测试失败：fast-model",
              result: {
                checks: [
                  {
                    role: "reasoning",
                    model: "reasoning-model",
                    status: "COMPLETED",
                    latency_ms: 120,
                    http_status: 200,
                    endpoint: "/v1/responses",
                    request_id: "req-reasoning",
                    message: "连接、鉴权、模型和结构化输出均通过",
                  },
                  {
                    role: "fast",
                    model: "fast-model",
                    status: "FAILED",
                    latency_ms: 80,
                    http_status: 403,
                    endpoint: "/v1/responses",
                    request_id: "req-fast-blocked",
                    error_type: "provider_blocked",
                    message: "New API 前置代理/WAF 拦截客户端请求；这不表示 API Key 无效。",
                  },
                ],
              },
              started_at: "2026-08-07T08:00:00Z",
              finished_at: "2026-08-07T08:00:01Z",
            },
          ]);
        }
        return response(basePayload(url));
      }),
    );

    render(<DashboardShell />);
    fireEvent.click(screen.getByRole("button", { name: /设置/ }));

    expect(await screen.findByText("推理模型", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("快速模型", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("reasoning-model", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText("fast-model", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText(/New API 前置代理\/WAF/)).toBeInTheDocument();
    expect(screen.getByText(/HTTP 403 · provider_blocked/)).toBeInTheDocument();
    expect(screen.getByText(/Request ID: req-fast-blocked/)).toBeInTheDocument();
  });
});
