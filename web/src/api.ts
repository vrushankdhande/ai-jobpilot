// One way for every page to call the backend: uniform errors, never throws, never shows internals. The UI holds NO business logic.
export interface Result<T = any> {
  ok: boolean;
  data?: T;
  error?: string;
  status?: number;
  problems: string[];
  detail?: Record<string, any> | null;
}

export interface CallOptions {
  params?: Record<string, string | number | boolean | null | undefined>;
  json?: unknown;
  form?: FormData;
  admin?: boolean;            // operational/config call: the proxy adds X-Admin-Token (the browser never holds it)
  timeoutMs?: number;
  signal?: AbortSignal;
}

export function explain(detail: unknown): { message: string; problems: string[]; detail: Record<string, any> | null } {
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const d = detail as Record<string, any>;
    return { message: String(d.message ?? "Request failed"), problems: (d.problems ?? []).map(String), detail: d };
  }
  if (Array.isArray(detail)) {
    const msg = detail.filter((d) => d && typeof d === "object")
      .map((d: any) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`).join("; ");
    return { message: msg || "Invalid request", problems: [], detail: null };
  }
  return { message: detail ? String(detail) : "Request failed", problems: [], detail: null };
}

export function queryString(params?: CallOptions["params"]): string {
  if (!params) return "";
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export async function call<T = any>(method: string, path: string, opts: CallOptions = {}): Promise<Result<T>> {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.json); }
  if (opts.admin) headers["X-Admin-Action"] = "1";
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), opts.timeoutMs ?? 90_000);
  opts.signal?.addEventListener("abort", () => ctl.abort());
  try {
    const resp = await fetch(path + queryString(opts.params), { method, headers, body, signal: ctl.signal, credentials: "same-origin" });   // only the HttpOnly sign-in cookie (scripts cannot read it)
    if (resp.status >= 400) {
      let e = { message: "Request failed", problems: [] as string[], detail: null as Record<string, any> | null };
      try { e = explain((await resp.json())?.detail); } catch { /* not JSON: keep the generic message */ }
      if (resp.status === 401 && e.detail?.code === "login_required" && typeof window !== "undefined") window.dispatchEvent(new Event("jobpilot:login-required"));   // signed out / session expired: show the sign-in page
      return { ok: false, error: `${e.message} (HTTP ${resp.status})`, status: resp.status, problems: e.problems, detail: e.detail };
    }
    const type = resp.headers.get("content-type") ?? "";
    if (type.includes("json")) return { ok: true, data: await resp.json(), status: resp.status, problems: [] };
    if (type.startsWith("text/") || type.includes("csv")) return { ok: true, data: (await resp.text()) as any, status: resp.status, problems: [] };
    return { ok: true, data: (await resp.blob()) as any, status: resp.status, problems: [] };
  } catch (err: any) {
    if (err?.name === "AbortError" && opts.signal?.aborted) return { ok: false, error: "cancelled", problems: [] };
    return { ok: false, error: "Backend unreachable. Is the API running?", problems: [] };
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  get: <T = any>(path: string, o?: CallOptions) => call<T>("GET", path, o),
  post: <T = any>(path: string, o?: CallOptions) => call<T>("POST", path, o),
  put: <T = any>(path: string, o?: CallOptions) => call<T>("PUT", path, o),
  patch: <T = any>(path: string, o?: CallOptions) => call<T>("PATCH", path, o),
  del: <T = any>(path: string, o?: CallOptions) => call<T>("DELETE", path, o),
};
