import { describe, expect, it } from "vitest";
import {
  compareTimestampsDesc,
  formatBeijingDateTime,
  formatElapsed,
  timestampMs,
} from "./datetime";

describe("Beijing time contract", () => {
  it("calculates the original 480-minute scenario as 20 seconds", () => {
    const now = Date.parse("2026-08-10T10:23:17+08:00");
    expect(formatElapsed("2026-08-10T10:22:57+08:00", null, now)).toBe("20 秒");
  });

  it("calculates completed and cross-day elapsed time from absolute instants", () => {
    expect(
      formatElapsed(
        "2026-08-10T23:59:30+08:00",
        "2026-08-11T00:01:00+08:00",
        0,
      ),
    ).toBe("1 分 30 秒");
  });

  it("sorts equivalent timezone representations by their absolute instant", () => {
    const values = [
      "2026-08-10T02:23:00Z",
      "2026-08-10T10:24:00+08:00",
      "2026-08-10T10:22:00+08:00",
    ].sort(compareTimestampsDesc);
    expect(values[0]).toBe("2026-08-10T10:24:00+08:00");
    expect(values[2]).toBe("2026-08-10T10:22:00+08:00");
  });

  it("rejects timestamps without an explicit timezone", () => {
    expect(timestampMs("2026-08-10T02:22:57")).toBeNull();
    expect(formatElapsed("2026-08-10T02:22:57", null, Date.now())).toBe("耗时未知");
  });

  it("formats absolute timestamps in Asia/Shanghai", () => {
    expect(formatBeijingDateTime("2026-08-10T02:22:57Z")).toContain("10:22:57");
  });
});
