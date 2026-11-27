import { useState } from "react";
import { Link } from "react-router-dom";
import { ActivityCharts } from "../components/ActivityCharts";
import { PageHeader } from "../components/Layout";
import { Alert, DataTable, Empty, ErrorBox, Loading, MetricRow, Progress } from "../components/ui";
import { fmtDt, schedulerState } from "../format";
import { useFetch } from "../hooks";
import { limitRows, usageRatio } from "../lib/automation";
import { headline, nextDiscovery, splitByValue, sumLastDays, type MatchCounts, type StatusItem } from "../lib/dashboard";

const DAYS = [7, 14, 30, 60];

/** Dashboard: every number comes from /api/dashboard/summary (computed from stored records). Nothing is hard-coded or sample data. */
export default function Dashboard() {
  const [days, setDays] = useState(14);
  const summary = useFetch("/api/dashboard/summary", { days });
  const status = useFetch("/api/scheduler/status");
  const counts = useFetch("/api/matching/counts");
  const d = summary.data;

  return (
    <>
      <PageHeader icon="📊" title="Dashboard" />
      <div className="row">
        <label className="field" style={{ minWidth: 220 }}><span>Activity window (days)</span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{DAYS.map((n) => <option key={n} value={n}>{n}</option>)}</select>
        </label>
        <button onClick={() => { summary.reload(); status.reload(); counts.reload(); }}>🔄 Refresh</button>
      </div>
      {summary.loading && !d && <Loading text="Loading metrics…" />}
      <ErrorBox result={summary.result} />
      {d && <Body d={d} status={status.data} counts={counts.data} />}
    </>
  );
}

function Body({ d, status, counts }: { d: any; status: any; counts: MatchCounts | null | undefined }) {
  const j = d.jobs, a = d.applications, iv = d.interviews, s = d.system, p = a.pending_actions;
  const series = (d.activity ?? []) as Array<Record<string, any>>;
  const top = headline(a.pending_actions_total || 0, counts, nextDiscovery(status), (v) => fmtDt(v));
  const apps = splitByValue(statusItems(a, iv, d.queue));
  const hasActivity = series.some((r) => (r.jobs_discovered || 0) + (r.applications_prepared || 0) + (r.applications_submitted || 0) > 0);
  const pending: Array<[string, number, string]> = [
    ["✋ Need your input", p.needs_your_input, "/application-queue"], ["👀 Ready to approve", p.ready_to_approve, "/application-queue"],
    ["✅ Approved, not submitted", p.approved_not_submitted, "/application-queue"], ["❓ Verify outcome", p.verify_outcome, "/applied-jobs"],
    ["🧑‍💻 Manual handoff", p.manual_handoff, "/application-queue"],
  ];
  return (
    <>
      <h2>Today</h2>
      <Alert kind={top.tone}>{top.text}{top.link && <> <Link to={top.link.to}>{top.link.label} →</Link></>}</Alert>
      <MetricRow items={[["New jobs today", j.discovered_today, "Jobs added today (discovered or imported)"],
        ["New jobs this week", sumLastDays(series, "jobs_discovered", 7), "Added in the last 7 days"],
        ["Good jobs to apply", counts ? <Link to="/job-matching">{counts.to_apply ?? 0}</Link> : "—", `Fit your preferences, eligible and score ≥ ${d.min_match_score}, not applied yet`],
        ["Applied this week", sumLastDays(series, "applications_submitted", 7), `${a.submitted} applied in total`]]} />

      <h2>Jobs</h2>
      <MetricRow items={[["Jobs discovered", j.total, "All jobs stored (discovered or imported)"], ["Good matches", j.matching_profile, `Fit your preferences, eligibility PASS and score ≥ ${d.min_match_score} (same as Job Matching)`],
        ["Average match score", j.average_match_score, `Mean of the latest score per job (${j.scored} scored)`],
        ["Eligible, not applied", counts?.eligible_not_applied ?? "—", "Eligible jobs (any score) that fit your preferences and you have not applied to"]]} />

      <h2>Applications</h2>
      {apps.shown.length > 0 && <MetricRow items={apps.shown.map((i) => [i.label, i.to ? <Link to={i.to}>{i.value}</Link> : i.value, i.help ?? null])} />}
      {apps.zero.length > 0 && (
        <p className="muted" data-testid="zero-status">
          At 0 right now (nothing waiting): {apps.zero.map((i, n) => <span key={i.label} title={i.help}>{n > 0 ? " · " : ""}{i.label}</span>)}
        </p>
      )}

      <h2>Daily activity</h2>
      {!hasActivity ? <Empty message="No activity in this window yet." hint="Run job discovery or prepare an application to see it here." /> : <ActivityCharts series={series} />}

      <div className="grid cols-2">
        <div>
          <h2>Pending actions</h2>
          {pending.every(([, n]) => !n) ? <Empty message="Nothing needs your attention." /> : pending.filter(([, n]) => n).map(([label, n, to]) => <div key={label}><Link to={to}>{label}: {n}</Link></div>)}
        </div>
        <div>
          <h2>Upcoming interviews</h2>
          {iv.next?.length ? <DataTable rows={iv.next} columns={[{ header: "When", render: (i: any) => fmtDt(i.scheduled_at) }, { header: "Company", render: (i: any) => i.company_name }, { header: "Role", render: (i: any) => i.job_title },
            { header: "Round", render: (i: any) => i.round }, { header: "Link", render: (i: any) => i.meeting_link || "" }]} /> : <Empty message="No interviews in the next 7 days." hint="Add one on the Interview Tracker page." />}
        </div>
      </div>

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

/** Application/queue boxes with a plain explanation of when each one fills. */
function statusItems(a: any, iv: any, q: any): StatusItem[] {
  return [
    { label: "Submitted", value: a.submitted || 0, help: "Confirmed by the site or recorded by you", to: "/applied-jobs" },
    { label: "Awaiting approval", value: a.awaiting_approval || 0, help: "Fills when a new eligible job scoring at or above your rule is prepared for your OK", to: "/application-queue" },
    { label: "Verified", value: a.verified || 0, help: "Fills when you add a confirmation (company e-mail or reference) in Applied Jobs", to: "/applied-jobs" },
    { label: "Pending actions", value: a.pending_actions_total || 0, help: "Need your input, review, verification or manual handoff", to: "/application-queue" },
    { label: "Upcoming interviews (7 days)", value: iv.upcoming_7_days || 0, help: "Add interviews on the Interview Tracker page", to: "/interview-tracker" },
    { label: "Failed", value: a.failed || 0, help: "Not submitted: failed or rejected by the site", to: "/application-queue" },
    { label: "Queue: awaiting approval", value: q?.awaiting_approval || 0, help: "Background queue: prepared and waiting for your approval", to: "/application-queue" },
    { label: "Queue: needs verification", value: q?.needs_verification || 0, help: "Background queue: outcome to check", to: "/application-queue" },
    { label: "Queue: failed", value: q?.failed || 0, help: "Background queue: preparation failed", to: "/automation" },
  ];
}
