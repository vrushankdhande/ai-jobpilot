import { createContext, useContext, useState, type ReactNode } from "react";
import { clampPage, pageCount } from "../format";
import type { Result } from "../api";

// ---------------------------------------------------------------- flash messages (survive a re-render within the page)
interface Flash { text: string; error: boolean }
const FlashCtx = createContext<{ flash: Flash | null; setFlash: (text: string, error?: boolean) => void; clear: () => void }>({ flash: null, setFlash: () => {}, clear: () => {} });
export function FlashProvider({ children }: { children: ReactNode }) {
  const [flash, set] = useState<Flash | null>(null);
  return <FlashCtx.Provider value={{ flash, setFlash: (text, error = false) => set({ text, error }), clear: () => set(null) }}>{children}</FlashCtx.Provider>;
}
export const useFlash = () => useContext(FlashCtx);
export function FlashBanner() {
  const { flash, clear } = useFlash();
  if (!flash) return null;
  return <div className={`alert ${flash.error ? "error" : "success"}`} role="status">{flash.text}<button className="link" onClick={clear} aria-label="Dismiss">✕</button></div>;
}

// ---------------------------------------------------------------- basic blocks
export function Alert({ kind = "info", children }: { kind?: "info" | "success" | "warning" | "error"; children: ReactNode }) {
  return <div className={`alert ${kind}`} role={kind === "error" ? "alert" : "status"}>{children}</div>;
}
export function ErrorBox({ result }: { result: Result<any> | null }) {
  if (!result || result.ok) return null;
  return <Alert kind="error">{result.error}{result.problems.length > 0 && <ul>{result.problems.map((p) => <li key={p}>{p}</li>)}</ul>}</Alert>;
}
export function Empty({ message, hint }: { message: string; hint?: string }) {
  return <div className="alert info empty">📭 {message}{hint && <div className="muted">{hint}</div>}</div>;
}
export function Loading({ text = "Loading…" }: { text?: string }) {
  return <div className="loading" role="progressbar" aria-busy="true"><span className="spinner" />{text}</div>;
}
export function Metric({ label, value, help }: { label: string; value: ReactNode; help?: string | null }) {
  return <div className="metric" data-testid="metric" title={help ?? undefined}><div className="metric-label">{label}</div><div className="metric-value">{value ?? "—"}</div>{help && <div className="metric-help">{help}</div>}</div>;
}
export function MetricRow({ items }: { items: Array<[string, ReactNode, (string | null)?]> }) {
  return <div className="metric-row">{items.map(([l, v, h]) => <Metric key={l} label={l} value={v} help={h} />)}</div>;
}
export function Badge({ children, kind = "neutral" }: { children: ReactNode; kind?: "neutral" | "good" | "warn" | "bad" }) {
  return <span className={`badge ${kind}`}>{children}</span>;
}
export function Progress({ value, text }: { value: number; text?: string }) {
  return <div className="progress"><div className="progress-text">{text}</div><div className="progress-bar"><div style={{ width: `${Math.round(value * 100)}%` }} /></div></div>;
}

// ---------------------------------------------------------------- table: plain text cells, never raw HTML
export interface Column<T> { header: string; render: (row: T) => ReactNode }
export function DataTable<T>({ rows, columns, rowKey }: { rows: T[]; columns: Column<T>[]; rowKey?: (row: T, i: number) => string }) {
  return (
    <div className="table-wrap"><table>
      <thead><tr>{columns.map((c) => <th key={c.header}>{c.header}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={rowKey ? rowKey(r, i) : i}>{columns.map((c) => <td key={c.header}>{c.render(r)}</td>)}</tr>)}</tbody>
    </table></div>
  );
}

// ---------------------------------------------------------------- pagination (the page is clamped when the result set shrinks)
export function Paginator({ total, size, page, onPage }: { total: number; size: number; page: number; onPage: (p: number) => void }) {
  const pages = pageCount(total, size);
  const cur = clampPage(page, pages);
  return (
    <div className="paginator">
      <button onClick={() => onPage(cur - 1)} disabled={cur <= 1}>◀ Previous</button>
      <span>Page <b>{cur}</b> of <b>{pages}</b> · {total} result(s)</span>
      <button onClick={() => onPage(cur + 1)} disabled={cur >= pages}>Next ▶</button>
    </div>
  );
}

// ---------------------------------------------------------------- two-step confirmation for anything destructive or irreversible
export function ConfirmButton({ label, question, onConfirm, kind = "danger", disabled }: { label: string; question: string; onConfirm: () => void; kind?: "danger" | "primary"; disabled?: boolean }) {
  const [asking, setAsking] = useState(false);
  if (!asking) return <button className={kind} disabled={disabled} onClick={() => setAsking(true)}>{label}</button>;
  return (
    <div className="confirm" role="alertdialog" aria-label={question}>
      <strong>{question}</strong>
      <button className={kind} onClick={() => { setAsking(false); onConfirm(); }}>Yes, confirm</button>
      <button onClick={() => setAsking(false)}>Cancel</button>
    </div>
  );
}

export function Check({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return <label className="check"><input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />{label}</label>;
}
export function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}
export function Tabs({ tabs, value, onChange }: { tabs: string[]; value: string; onChange: (t: string) => void }) {
  return <div className="tabs" role="tablist">{tabs.map((t) => <button key={t} role="tab" aria-selected={t === value} onClick={() => onChange(t)}>{t}</button>)}</div>;
}
