// Pure display helpers (unit-tested). No fake values are ever produced: missing -> an em dash.
export const DASH = "—";

export function pageCount(total: number, size: number): number {
  return Math.max(1, Math.ceil(Math.max(0, total) / Math.max(1, size)));
}
export function clampPage(page: number, pages: number): number {
  return Math.min(Math.max(1, page), Math.max(1, pages));
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function fmtDt(value: unknown, withTime = true): string {
  if (!value) return DASH;
  const d = new Date(String(value));
  if (Number.isNaN(d.getTime())) return String(value).slice(0, 16);
  const p = (n: number) => String(n).padStart(2, "0");
  const date = `${p(d.getDate())} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
  return withTime ? `${date} ${p(d.getHours())}:${p(d.getMinutes())}` : date;
}
export function schedulerState(s: { enabled?: boolean; paused?: boolean }): string {
  if (s.enabled && s.paused) return "⏸️ Paused";
  return s.enabled ? "🟢 Running" : "⚪ Stopped";
}
export function usageRatio(used: number, allowed: number): number {
  return allowed > 0 ? Math.min(1, Math.max(0, used / allowed)) : 0;
}
export const show = (v: unknown): string => (v === null || v === undefined || v === "" ? DASH : String(v));
export function downloadBlob(name: string, data: Blob | string, type = "text/csv") {
  const blob = data instanceof Blob ? data : new Blob([data], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}
