export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

const BASE = "/api/v1";
let refreshing: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  // One refresh at a time, shared by every request that got a 401
  refreshing ??= fetch(`${BASE}/auth/refresh`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-Requested-With": "lumen" },
  })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => setTimeout(() => (refreshing = null), 0));
  return refreshing;
}

function errorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      // FastAPI validation errors
      return detail
        .map((d: { loc?: string[]; msg?: string }) => `${d.loc?.slice(1).join(".") ?? ""} ${d.msg ?? ""}`.trim())
        .join("; ");
    }
  }
  return fallback;
}

export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown; form?: FormData; retry?: boolean } = {},
): Promise<T> {
  const { method = "GET", body, form, retry = true } = options;
  const headers: Record<string, string> = { "X-Requested-With": "lumen" };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });

  if (res.status === 401 && retry && !path.startsWith("/auth/")) {
    if (await tryRefresh()) return api<T>(path, { ...options, retry: false });
  }
  if (res.status === 204) return undefined as T;

  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, errorMessage(data, `Request failed (${res.status})`));
  return data as T;
}

export const get = <T>(path: string) => api<T>(path);
export const post = <T>(path: string, body?: unknown) => api<T>(path, { method: "POST", body });
export const patch = <T>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body });
export const put = <T>(path: string, body: unknown) => api<T>(path, { method: "PUT", body });
export const del = (path: string) => api<void>(path, { method: "DELETE" });
