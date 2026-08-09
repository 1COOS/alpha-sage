export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:7777";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function readableDetail(value: unknown): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    const object = value as Record<string, unknown>;
    if (typeof object.message === "string") return object.message;
    if (Array.isArray(object.checks)) {
      const blocked = object.checks
        .filter((item) => item && typeof item === "object" && !(item as Record<string, unknown>).passed)
        .map((item) => String((item as Record<string, unknown>).detail ?? "检查未通过"));
      if (blocked.length) return blocked.join("；");
    }
  }
  return JSON.stringify(value);
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.text();
    let detail: unknown = body;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      detail = parsed.detail ?? parsed;
    } catch {
      // Keep the original text for non-JSON upstream errors.
    }
    throw new ApiError(
      response.status,
      readableDetail(detail) || `${response.status} ${response.statusText}`,
      detail,
    );
  }
  return (await response.json()) as T;
}

export function money(value: string | number | undefined): string {
  const amount = Number(value ?? 0);
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 0,
  }).format(amount);
}

export function percent(value: string | number | undefined): string {
  return `${(Number(value ?? 0) * 100).toFixed(2)}%`;
}
