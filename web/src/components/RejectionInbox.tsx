import { useState } from "react";
import { api } from "../api";
import { fmtDt } from "../format";
import { useFetch } from "../hooks";
import { appOption, appliedPath, lastCheckText, rejectionTitle } from "../lib/applied";
import { Alert, ErrorBox, useFlash } from "./ui";

const TRACKED = ["SUBMITTED", "VERIFIED", "VERIFICATION_PENDING", "MANUAL_HANDOFF_REQUIRED"];

/** Rejection e-mails found in Gmail: the ones to confirm, the ones that matched nothing, and the ones marked automatically (with Undo). */
export function RejectionInbox({ onChanged }: { onChanged: () => void }) {
  const ov = useFetch<any>("/api/applications/rejections");
  const { setFlash } = useFlash();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const d = ov.data;
  const toConfirm: any[] = d?.needs_confirmation ?? [];
  const unmatched: any[] = d?.unmatched ?? [];
  const recent: any[] = d?.recent ?? [];
  const all = useFetch<any>(unmatched.length ? appliedPath("/api/applications/page", { limit: 200, dismissed: false }, TRACKED) : null);
  const allApps: any[] = all.data?.items ?? [];
  const changed = () => { ov.reload(); onChanged(); };

  async function run(fn: () => Promise<any>, ok: string) {
    setBusy(true); setErr(null);
    const r = await fn();
    setBusy(false);
    if (r.ok) { setFlash(ok); changed(); } else setErr(r.error);
  }
  const check = () => run(() => api.post("/api/applications/rejections/check", { admin: true }), "Gmail checked for rejection e-mails.");
  const confirm = (sid: string, appId: string) => run(() => api.post(`/api/applications/rejections/${sid}/confirm`, { json: { application_id: appId } }), "Marked as rejected by the employer.");
  const dismiss = (sid: string) => run(() => api.post(`/api/applications/rejections/${sid}/dismiss`), "Removed from the list.");
  const addApplied = (sid: string) => run(() => api.post(`/api/applications/rejections/${sid}/add-as-applied`), "Added to Applied Jobs and marked as rejected.");
  const addable = unmatched.filter((r) => r.company && r.role);
  async function addAll() {
    setBusy(true); setErr(null);
    let ok = 0; const failed: string[] = [];
    for (const r of addable) {
      const res = await api.post(`/api/applications/rejections/${r.signal_id}/add-as-applied`);
      if (res.ok) ok++; else failed.push(`${rejectionTitle(r)}: ${res.error}`);
    }
    setBusy(false);
    if (ok) setFlash(`${ok} application(s) added to Applied Jobs and marked as rejected.`);
    if (failed.length) setErr(failed.join(" · "));
    changed();
  }
  const undo = (appId: string) => run(() => api.post(`/api/applications/${appId}/employer-rejected/undo`), "Rejection removed from the application.");

  return (
    <section className="card" aria-label="Rejection e-mails">
      <h3>📧 Rejection e-mails from Gmail</h3>
      <ErrorBox result={ov.result} />
      {d && <p className="muted">{lastCheckText(d)}</p>}
      {d?.connected && <button type="button" disabled={busy} onClick={check}>🔄 Check mail now</button>}
      {err && <Alert kind="error">{err}</Alert>}
      {toConfirm.length > 0 && (
        <>
          <Alert kind="warning">{toConfirm.length} rejection e-mail(s) need you: the company matches, but JobPilot is not sure which application it is.</Alert>
          {toConfirm.map((r) => <Pick key={r.signal_id} row={r} options={r.candidates} busy={busy} onYes={confirm} onNo={dismiss} />)}
        </>
      )}
      {unmatched.length > 0 && (
        <details className="fold">
          <summary>E-mails not matched to any application ({unmatched.length})</summary>
          <p className="muted">These say &quot;not selected&quot; but name a company you have no application for here (perhaps you applied outside JobPilot). Pick the application or remove the e-mail from the list.</p>
          <p className="muted">Applied outside JobPilot? <b>➕ Add to Applied Jobs</b> records it as applied by you (only the company and role from the e-mail) and marks it rejected.</p>
          {addable.length > 1 && <button type="button" disabled={busy} onClick={addAll}>➕ Add all {addable.length} to Applied Jobs as rejected</button>}
          {unmatched.map((r) => <Pick key={r.signal_id} row={r} options={allApps} busy={busy} onYes={confirm} onNo={dismiss} onAdd={r.company && r.role ? addApplied : undefined} />)}
        </details>
      )}
      {recent.length > 0 && (
        <details className="fold">
          <summary>Marked from e-mail ({recent.length})</summary>
          <ul>
            {recent.map((r) => (
              <li key={r.signal_id}>
                <b>{r.application ? appOption(r.application) : rejectionTitle(r)}</b> · {fmtDt(r.received_at, false)} · “{r.phrase}” · from {r.sender_domain}
                {r.decided_by === "email_check" ? " · automatic" : " · confirmed by you"}{" "}
                {r.application && <button type="button" className="link-btn" disabled={busy} onClick={() => undo(r.application.application_id)}>Undo</button>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function Pick({ row, options, busy, onYes, onNo, onAdd }: { row: any; options: any[]; busy: boolean; onYes: (sid: string, appId: string) => void; onNo: (sid: string) => void; onAdd?: (sid: string) => void }) {
  const [appId, setAppId] = useState<string>(options[0]?.application_id ?? "");
  const value = options.some((o) => o.application_id === appId) ? appId : (options[0]?.application_id ?? "");
  return (
    <div className="card" data-testid="rejection-row">
      <p><b>{rejectionTitle(row)}</b> · {fmtDt(row.received_at, false)} · from {row.sender_domain}<br /><span className="muted">“{row.phrase}” · {row.reason}</span></p>
      <label>Which application?{" "}
        <select value={value} onChange={(e) => setAppId(e.target.value)} disabled={!options.length}>
          {options.map((o) => <option key={o.application_id} value={o.application_id}>{appOption(o)}</option>)}
        </select>
      </label>{" "}
      <button type="button" disabled={busy || !value} onClick={() => onYes(row.signal_id, value)}>❌ Yes, mark it rejected</button>{" "}
      {onAdd && <><button type="button" disabled={busy} onClick={() => onAdd(row.signal_id)}>➕ Add to Applied Jobs</button>{" "}</>}
      <button type="button" disabled={busy} onClick={() => onNo(row.signal_id)}>Not mine</button>
    </div>
  );
}
