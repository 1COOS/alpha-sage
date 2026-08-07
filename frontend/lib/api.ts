export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:7777";

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
    throw new Error(body || `${response.status} ${response.statusText}`);
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

