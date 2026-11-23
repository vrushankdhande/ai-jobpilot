import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Result } from "../api";
import { PageHeader } from "../components/Layout";
import { Alert, DataTable, Empty, ErrorBox, Field, Loading, type Column } from "../components/ui";
import { schedulerState } from "../format";
import { useFetch, useWebConfig } from "../hooks";
import { QUEUE_LABELS, canControl, failureRows, jobRows, limitRows, notificationRows, queueRows, reportSummary } from "../lib/automation";

/**
 * Automation: control the scheduler, watch the application queue, adjust (lower) limits, see failures, notifications and daily reports.
 * Controls need ADMIN_TOKEN (held by the web server's proxy); without it the page is read-only.
 * Nothing here can submit an application - approval and submission stay on the Application Queue page.
 */
const table = <T extends Record<string, any>>(rows: T[], headers: Array<[string, keyof T]>) =>
  <DataTable rows={rows} columns={headers.map(([header, key]): Column<T> => ({ header, render: (r) => (r[key] === null || r[key] === undefined ? "" : String(r[key])) }))} />;

export default function Automation() {
  const web = useWebConfig();
  const ctl = web.adminEnabled;
  const status = useFetch<any>("/api/scheduler/status", undefined, { admin: true });
  const queue = useFetch<any>("/api/queue", undefined, { admin: true });
  const notes = useFetch<any>("/api/notifications", { limit: 25 }, { admin: true });
  const reports = useFetch<any[]>("/api/reports/daily", { limit: 7 }, { admin: true });
  const [err, setErr] = useState<string | null>(null);
  const reloadAll = () => { status.reload(); queue.reload(); notes.reload(); reports.reload(); };

  /** Run an operational call; reload on success, show the server's message otherwise. */
  const act = async (method: "POST" | "PUT", path: string, json?: unknown): Promise<Result> => {
    setErr(null);
    const r = method === "POST" ? await api.post(path, { admin: true, json }) : await api.put(path, { admin: true, json });
    if (r.ok) reloadAll(); else setErr(r.error ?? "Request failed");
    return r;
  };

  if (status.loading && !status.result) return <><PageHeader icon="🤖" title="Automation" /><Loading /></>;
  if (!status.result?.ok) return <><PageHeader icon="🤖" title="Automation" /><ErrorBox result={status.result} /></>;
  const s = status.data;

  return (
    <>
      <PageHeader icon="🤖" title="Automation" />
      {web.loaded && !ctl && <Alert kind="warning">ADMIN_TOKEN is not set, so controls are disabled (read-only). Add ADMIN_TOKEN=&lt;a long random string&gt; to .env and restart the API and the web server.</Alert>}
      {err && <Alert kind="error">{err}</Alert>}

      <h2>1. Scheduler</h2>
      <p><b>{schedulerState(s)}</b> · time zone {s.timezone} · now {String(s.now_local).slice(0, 16).replace("T", " ")}</p>
      <div className="row">
        {([["▶️ Start", "/api/scheduler/start", s.enabled], ["⏹️ Stop", "/api/scheduler/stop", !s.enabled], ["⏸️ Pause automation", "/api/scheduler/pause", !s.enabled || s.paused],
          ["⏯️ Resume automation", "/api/scheduler/resume", !s.paused], ["🔎 Discover now", "/api/scheduler/discover", false]] as Array<[string, string, boolean]>).map(([label, path, dis]) => (
          <button key={path} disabled={!ctl || dis} onClick={() => act("POST", path)}>{label}</button>
        ))}
      </div>
      <div className="row" style={{ marginTop: 8 }}><button disabled={!ctl} onClick={() => act("POST", "/api/scheduler/worker/run")}>⚙️ Prepare queued applications now</button></div>
      {table(jobRows(s), [["Job", "job"], ["Schedule", "schedule"], ["Last run", "lastRun"], ["Next run", "nextRun"], ["Running", "running"], ["Last status", "lastStatus"], ["Last error", "lastError"]])}
      <p className="muted">Discovery only uses sources your policy file permits (LinkedIn, Indeed and Naukri are manual-import only). The worker only PREPARES applications; you review and finish them on the Application Queue page.</p>

      <h2>2. Daily limits</h2>
      {table(limitRows(s), [["Limit", "limit"], ["Used", "used"], ["Allowed", "allowed"], ["Cap", "cap"]])}
      <LimitsForm key={JSON.stringify(s.limits.effective)} status={s} ctl={ctl} onSave={(limits) => act("PUT", "/api/scheduler/limits", { limits })} />

      <h2>3. Application queue</h2>
      <QueueSection queue={queue} ctl={ctl} act={act} />

      <h2>4. Failures and retries</h2>
      {queue.data && (failureRows(queue.data.tasks).length > 0
        ? table(failureRows(queue.data.tasks), [["Job", "job"], ["Company", "company"], ["Status", "status"], ["Attempts", "attempts"], ["Error", "error"], ["Updated", "updated"]])
        : <Alert kind="success">No failed or unverified tasks.</Alert>)}
      <p className="muted">Temporary failures are retried with backoff up to the retry limit. An application whose outcome is uncertain is never retried - please verify it manually.</p>

      <h2>5. Notifications</h2>
      <NotificationsSection notes={notes} ctl={ctl} />

      <h2>6. Daily report</h2>
      <ReportSection reports={reports} ctl={ctl} act={act} />
    </>
  );
}

// ---------------------------------------------------------------- limits (you can only LOWER them; the caps come from the YAML)
function LimitsForm({ status, ctl, onSave }: { status: any; ctl: boolean; onSave: (limits: Record<string, number>) => void }) {
  const eff = status.limits.effective, caps = status.limits.caps;
  const fields: Array<[string, string, number, number]> = [
    ["max_jobs_discovered_per_day", "Jobs discovered / day", 0, caps.max_jobs_discovered_per_day], ["max_jobs_processed_per_day", "Jobs prepared / day", 0, caps.max_jobs_processed_per_day],
    ["max_applications_per_day", "Applications submitted / day", 0, caps.max_applications_per_day], ["max_retries_per_application", "Retries / application", 0, caps.max_retries_per_application],
    ["min_match_score", "Minimum match score", Number(caps.min_match_score), 100],
  ];
  const [v, setV] = useState<Record<string, number>>(() => Object.fromEntries(fields.map(([k]) => [k, Number(eff[k])])));
  return (
    <form onSubmit={(e) => { e.preventDefault(); onSave(Object.fromEntries(Object.entries(v).map(([k, n]) => [k, Number(n)]))); }}>
      <p className="muted">You can LOWER limits here (and raise the minimum match score). The caps come from config/automation_limits.yaml and cannot be exceeded.</p>
      <div className="grid cols-3">
        {fields.map(([k, label, min, max]) => (
          <Field key={k} label={label}><input type="number" min={min} max={max} value={v[k]} onChange={(e) => setV({ ...v, [k]: Number(e.target.value) })} /></Field>
        ))}
      </div>
      <button type="submit" disabled={!ctl}>Save limits</button>
    </form>
  );
}

// ---------------------------------------------------------------- queue: pause / resume / cancel only
function QueueSection({ queue, ctl, act }: { queue: ReturnType<typeof useFetch<any>>; ctl: boolean; act: (m: "POST" | "PUT", p: string) => Promise<Result> }) {
  const [pick, setPick] = useState("");
  if (queue.loading && !queue.result) return <Loading />;
  if (!queue.result?.ok) return <ErrorBox result={queue.result} />;
  const q = queue.data, tasks = q.tasks as any[];
  const counts = Object.entries(q.counts as Record<string, number>).filter(([, n]) => n);
  const cur = tasks.find((t) => t.task_id === pick) ?? tasks[0];
  return (
    <>
      <p>{counts.length ? counts.map(([k, n]) => `${QUEUE_LABELS[k] ?? k}: ${n}`).join("  ·  ") : "The queue is empty."}</p>
      {tasks.length === 0 ? <Empty message="Nothing is queued or processing." /> : (
        <>
          {table(queueRows(tasks), [["Job", "job"], ["Company", "company"], ["Score", "score"], ["Eligibility", "eligibility"], ["Status", "status"], ["Attempts", "attempts"], ["Next attempt", "nextAttempt"], ["Last error", "lastError"]])}
          <Field label="Select a task">
            <select value={cur.task_id} onChange={(e) => setPick(e.target.value)}>
              {tasks.map((t) => <option key={t.task_id} value={t.task_id}>{`${t.job_title || "(untitled)"} - ${t.company_name || ""} [${t.status}]`}</option>)}
            </select>
          </Field>
          <div className="row">
            {([["pause", "⏸️ Pause"], ["resume", "▶️ Resume"], ["cancel", "🚫 Cancel"]] as const).map(([action, label]) => (
              <button key={action} disabled={!ctl || !canControl(action, cur.status)} onClick={() => act("POST", `/api/queue/${cur.task_id}/${action}`)}>{label}</button>
            ))}
          </div>
          {cur.status === "awaiting_approval" && <Alert>This application is prepared. Open the <Link to="/application-queue">Application Queue</Link> page to review it.</Alert>}
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------- notifications
function NotificationsSection({ notes, ctl }: { notes: ReturnType<typeof useFetch<any>>; ctl: boolean }) {
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  if (notes.loading && !notes.result) return <Loading />;
  if (!notes.result?.ok) return <ErrorBox result={notes.result} />;
  const cfg = notes.data.config;
  const test = async () => {
    const r = await api.post("/api/notifications/test", { admin: true });
    setMsg({ ok: r.ok, text: r.ok ? `Test e-mail: ${r.data.status}` : r.error ?? "Request failed" });
  };
  return (
    <>
      <p>✉️ {cfg.configured ? "Configured" : "Not configured - the system works without e-mail"}{cfg.recipient ? ` → ${cfg.recipient}` : ""}</p>
      <button disabled={!ctl || !cfg.configured} onClick={test}>Send a test e-mail</button>
      {msg && <Alert kind={msg.ok ? "info" : "error"}>{msg.text}</Alert>}
      {notes.data.notifications.length > 0 && table(notificationRows(notes.data.notifications), [["When", "when"], ["Type", "type"], ["Subject", "subject"], ["Status", "status"], ["Attempts", "attempts"], ["Note", "note"]])}
    </>
  );
}

// ---------------------------------------------------------------- daily report
function ReportSection({ reports, ctl, act }: { reports: ReturnType<typeof useFetch<any[]>>; ctl: boolean; act: (m: "POST" | "PUT", p: string, j?: unknown) => Promise<Result> }) {
  const [email, setEmail] = useState(false);
  const [sel, setSel] = useState(0);
  const reps = reports.data ?? [];
  const rep = reps[Math.min(sel, Math.max(0, reps.length - 1))];
  return (
    <>
      <div className="row">
        <button disabled={!ctl} onClick={() => act("POST", "/api/reports/daily/generate", { email })}>📈 Generate today&apos;s report</button>
        <label className="check"><input type="checkbox" checked={email} onChange={(e) => setEmail(e.target.checked)} />E-mail it too</label>
      </div>
      {reports.loading && !reports.result && <Loading />}
      {reports.result && !reports.result.ok && <ErrorBox result={reports.result} />}
      {reports.result?.ok && reps.length === 0 && <Empty message="No reports yet." />}
      {rep && (
        <>
          <Field label="Report">
            <select value={sel} onChange={(e) => setSel(Number(e.target.value))}>
              {reps.map((r, i) => <option key={i} value={i}>{`${r.date} (generated ${String(r.generated_at).slice(0, 16).replace("T", " ")})`}</option>)}
            </select>
          </Field>
          {table(reportSummary(rep), [["Metric", "metric"], ["Value", "value"]])}
          <p className="muted">Limit usage: {Object.entries(rep.limits as Record<string, any>).map(([k, v]) => `${k.replaceAll("_", " ")} ${v.used}/${v.limit}`).join(" · ")} — {rep.applications.note}</p>
        </>
      )}
    </>
  );
}
