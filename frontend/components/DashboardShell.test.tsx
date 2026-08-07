import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DashboardShell } from "./DashboardShell";

vi.stubGlobal(
  "fetch",
  vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("/system/status")
      ? {
          account_enabled: false,
          account_cash: "1000000",
          equity: "1000000",
          drawdown: "0",
          current_strategy: "alpha-sage-cognition-v1",
          last_run: null,
          source_health: [],
          blockers: ["尚未完成初始化自检"],
        }
      : url.endsWith("/portfolio")
        ? {
            account: { enabled: false, cash: "1000000" },
            positions: [],
            cash: "1000000",
            market_value: "0",
            equity: "1000000",
            drawdown: "0",
            risk_state: "NORMAL",
            horizon_values: {},
          }
        : [];
    return { ok: true, json: async () => payload };
  }),
);

describe("DashboardShell", () => {
  it("renders the local investment workbench navigation", () => {
    render(<DashboardShell />);
    expect(screen.getByText("ALPHA SAGE")).toBeInTheDocument();
    expect(screen.getAllByText("今日驾驶舱").length).toBeGreaterThan(0);
    expect(screen.getByText("进化中心")).toBeInTheDocument();
  });
});
