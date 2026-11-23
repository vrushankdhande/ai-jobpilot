import { useCallback, useEffect, useRef, useState } from "react";
import { api, type CallOptions, type Result } from "./api";

export interface Fetched<T> { loading: boolean; result: Result<T> | null; reload: () => void; data: T | undefined }

/** GET a path; re-fetches when path/params change or reload() is called. Stale responses are ignored. */
export function useFetch<T = any>(path: string | null, params?: CallOptions["params"], opts: { admin?: boolean } = {}): Fetched<T> {
  const [result, setResult] = useState<Result<T> | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [tick, setTick] = useState(0);
  const key = JSON.stringify([path, params, tick]);
  const seq = useRef(0);
  useEffect(() => {
    if (path === null) { setLoading(false); return; }
    const mine = ++seq.current;
    setLoading(true);
    api.get<T>(path, { params, admin: opts.admin }).then((r) => { if (mine === seq.current) { setResult(r); setLoading(false); } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { loading, result, reload, data: result?.ok ? result.data : undefined };
}

export function useLocalState<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try { const s = sessionStorage.getItem(key); return s ? (JSON.parse(s) as T) : initial; } catch { return initial; }
  });
  const set = (next: T) => { setV(next); try { sessionStorage.setItem(key, JSON.stringify(next)); } catch { /* storage unavailable: fine */ } };
  return [v, set];
}

/** The web server's public facts (never the token). */
export function useWebConfig(): { adminEnabled: boolean; loaded: boolean } {
  const [cfg, setCfg] = useState({ adminEnabled: false, loaded: false });
  useEffect(() => {
    fetch("/web/config").then((r) => r.json()).then((c) => setCfg({ adminEnabled: Boolean(c.adminEnabled), loaded: true })).catch(() => setCfg({ adminEnabled: false, loaded: true }));
  }, []);
  return cfg;
}
