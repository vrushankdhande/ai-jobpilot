import { useState } from "react";
import { Link } from "react-router-dom";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { PageHeader } from "../components/Layout";
import { DataTable, Empty, ErrorBox, Loading, MetricRow, Progress } from "../components/ui";
import { fmtDt, schedulerState } from "../format";
import { useFetch } from "../hooks";
import { limitRows, usageRatio } from "../lib/automation";

const DAYS = [7, 14, 30, 60];

/** Dashboard: every number comes from /api/dashboard/summary (computed from stored records). Nothing is hard-coded or sample data. */
export default function Dashboard() {
  const [days, setDays] = useState(14);
  const summary = useFetch("/api/dashboard/summary", { days });
  const status = useFetch("/api/scheduler/status");
  const d = summary.data;

  return (
    <>
      <PageHeader icon="📊" title="Dashboard" />
      <div className="row">
        <label className="field" style={{ minWidth: 220 }}><span>Activity window (days)</span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{DAYS.map((n) => <option key={n} value={n}>{n}</option>)}</select>
        </label>
        <button onClick={() => { summary.reload(); status.reload(); }}>🔄 Refresh</button>
      </div>
      {summary.loading && !d && <Loading text="Loading metrics…" />}
      <ErrorBox result={summary.result} />
      {d && <Body d={d} status={status.data} />}
    </>
  );
}

function Body({ d, status }: { d: any; status: any }) {
  const j = d.jobs, a = d.applications, iv = d.interviews, s = d.system, p = a.pending_actions;
  const series = (d.activity ?? []) as Array<Record<string, any>>;
  const hasActivity = series.some((r) => (r.jobs_discovered || 0) + (r.applications_prepared || 0) + (r.applications_submitted || 0) > 0);
  const pending: Array<[string, number, string]> = [
    ["✋ Need your input", p.needs_your_input, "/application-queue"], ["👀 Ready to approve", p.ready_to_approve, "/application-queue"],
    ["✅ Approved, not submitted", p.approved_not_submitted, "/application-queue"], ["❓ Verify outcome", p.verify_outcome, "/applied-jobs"],
    ["🧑‍💻 Manual handoff", p.manual_handoff, "/application-queue"],
  ];
  return (
    <>
      <h2>Jobs</h2>
      <MetricRow items={[["Jobs discovered", j.total, "All jobs stored (discovered or imported)"], ["Good matches", j.matching_profile, `Fit your preferences, eligibility PASS and score ≥ ${d.min_match_score} (same as Job Matching)`],
        ["Average match score", j.average_match_score, `Mean of the latest score per job (${j.scored} scored)`], ["Discovered today", j.discovered_today]]} />
      <h2>Applications</h2>
      <MetricRow items={[["Awaiting approval", a.awaiting_approval, "Prepared / needs input / approved but not submitted"], ["Submitted", a.submitted, "Confirmed by the site or recorded by you"],
        ["Verified", a.verified], ["Failed", a.failed, "Not submitted: failed or rejected by the site"], ["Pending actions", a.pending_actions_total, "Need your input, review, verification or manual handoff"]]} />
      <MetricRow items={[["Upcoming interviews (7 days)", iv.upcoming_7_days], ["Queue: awaiting approval", d.queue?.awaiting_approval ?? 0], ["Queue: needs verification", d.queue?.needs_verification ?? 0], ["Queue: failed", d.queue?.failed ?? 0]]} />

      <div className="grid dash-split">
        <div>
          <h2>Daily activity</h2>
          {!hasActivity ? <Empty message="No activity in this window yet." hint="Run job discovery or prepare an application to see it here." /> : (
            <div style={{ height: 280 }}><ResponsiveContainer><LineChart data={series}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="date" /><YAxis allowDecimals={false} /><Tooltip /><Legend />
              <Line type="monotone" dataKey="jobs_discovered" name="Jobs discovered" stroke="#2563eb" dot={false} />
              <Line type="monotone" dataKey="applications_prepared" name="Applications prepared" stroke="#d97706" dot={false} />
              <Line type="monotone" dataKey="applications_submitted" name="Applications submitted" stroke="#16794c" dot={false} /></LineChart></ResponsiveContainer></div>
          )}
        </div>
        <div>
          <h2>Pending actions</h2>
          {pending.every(([, n]) => !n) ? <Empty message="Nothing needs your attention." /> : pending.filter(([, n]) => n).map(([label, n, to]) => <div key={label}><Link to={to}>{label}: {n}</Link></div>)}
        </div>
      </div>

      <h2>Upcoming interviews</h2>
      {iv.next?.length ? <DataTable rows={iv.next} columns={[{ header: "When", render: (i: any) => fmtDt(i.scheduled_at) }, { header: "Company", render: (i: any) => i.company_name }, { header: "Role", render: (i: any) => i.job_title },
        { header: "Round", render: (i: any) => i.round }, { header: "Link", render: (i: any) => i.meeting_link || "" }]} /> : <Empty message="No interviews in the next 7 days." hint="Add one on the Interview Tracker page." />}

      <div className="grid cols-2">
        <div>
          <h2>Recent activity</h2>
          {d.recent?.length ? <DataTable rows={d.recent} columns={[{ header: "When", render: (r: any) => fmtDt(r.at) }, { header: "Type", render: (r: any) => r.kind }, { header: "What", render: (r: any) => r.text }]} /> : <Empty message="No activity recorded yet." />}
        </div>
        <div>
          <h2>System status</h2>
          <p>🗄️ Database: <b>{s.database}</b></p>
          <p>⏱️ Scheduler: <b>{schedulerState({ enabled: s.scheduler_enabled, paused: s.scheduler_paused })}</b>{s.last_discovery ? ` · last discovery ${fmtDt(s.last_discovery)}` : " · no discovery run yet"}</p>
          <p>🤖 Browser automation: <b>{s.automation_enabled ? "on" : "off (manual handoff only)"}</b></p>
          <p>✉️ E-mail notifications: <b>{s.notifications_enabled ? "on" : "off"}</b></p>
          <p>🧠 AI provider key: <b>{s.ai_key_configured ? "configured" : "not configured (rule-based mode)"}</b></p>
          <p className="muted">Time zone {d.timezone} · generated {fmtDt(d.generated_at)}</p>
        </div>
      </div>

      {status && (
        <details><summary>Today&apos;s automation limits</summary>
          {limitRows(status).slice(0, 2).map((r) => <Progress key={r.limit} value={usageRatio(r.used, r.allowed)} text={`${r.limit}: ${r.used} / ${r.allowed} (cap ${r.cap})`} />)}
          <p className="muted">Daily target {status.automation_target} is a goal, not a quota. Nothing is submitted without your approval.</p>
        </details>
      )}
    </>
  );
}
