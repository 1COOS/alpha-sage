const EXPLICIT_TIMEZONE = /(Z|[+-]\d{2}:\d{2})$/i;

export const BEIJING_TIME_ZONE = "Asia/Shanghai";

export function timestampMs(value: string): number | null {
  if (!EXPLICIT_TIMEZONE.test(value)) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function compareTimestampsDesc(left: string, right: string): number {
  const leftTime = timestampMs(left);
  const rightTime = timestampMs(right);
  if (leftTime == null && rightTime == null) return 0;
  if (leftTime == null) return 1;
  if (rightTime == null) return -1;
  return rightTime - leftTime;
}

export function formatElapsed(
  startedAt: string,
  finishedAt: string | null | undefined,
  now: number,
): string {
  const started = timestampMs(startedAt);
  const ended = finishedAt ? timestampMs(finishedAt) : now;
  if (started == null || ended == null || !Number.isFinite(ended)) return "耗时未知";
  const seconds = Math.max(0, Math.floor((ended - started) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} 分 ${seconds % 60} 秒`;
}

export function formatBeijingDateTime(value: string): string {
  const parsed = timestampMs(value);
  if (parsed == null) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: BEIJING_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
}
