import { Link } from "react-router-dom";
import { PAGES, PageHeader } from "../components/Layout";
import SearchNow from "../components/SearchNow";
import { Alert, Loading } from "../components/ui";
import { useFetch } from "../hooks";

const DESCRIPTIONS: Record<string, string> = {
  "/dashboard": "metrics, activity and system status", "/job-discovery": "find, filter and import jobs", "/resume-manager": "upload, review and correct your profile",
  "/job-matching": "score a job against your profile", "/document-generation": "resume, cover letter, answers", "/application-queue": "review, approve, reject, hand off",
  "/applied-jobs": "history, evidence and notes", "/interview-tracker": "schedule and prepare", "/analytics": "trends, rates and exports",
  "/automation": "scheduler, queue and limits", "/settings": "preferences, limits and status",
};

// The usual order of work, shown as a guide. Each step links to the page where it is done.
const WORKFLOW: Array<[string, string, string]> = [
  ["1", "Review your profile", "/resume-manager"], ["2", "Set your preferences", "/settings"], ["3", "Find jobs", "/job-discovery"],
  ["4", "Score the fit", "/job-matching"], ["5", "Prepare documents", "/document-generation"], ["6", "Approve and apply", "/application-queue"],
];

function Stat({ label, value, to, help }: { label: string; value: number | string; to: string; help?: string }) {
  return <Link to={to} className="home-stat" title={help}><span className="home-stat-value">{value}</span><span className="home-stat-label">{label}</span></Link>;
}

export default function Home() {
  const health = useFetch("/health");
  const db = useFetch("/health/database");
  const summary = useFetch("/api/dashboard/summary", { days: 14 });
  const counts = useFetch<any>("/api/matching/counts").data;
  const info = health.data ?? {};
  const d = summary.data;
  const reload = () => { health.reload(); db.reload(); summary.reload(); };
  const pendingTotal = d?.applications?.pending_actions_total ?? 0;

  return (
    <>
      <PageHeader icon="🧭" title="AI JobPilot" />
      <section className="home-hero">
        <p>AI-powered job discovery, matching and application assistant. <strong>Nothing is submitted without your explicit approval.</strong></p>
        <div className="row">
          <Link className="home-cta" to="/job-discovery">🔎 Find jobs</Link>
          <Link className="home-cta secondary" to="/application-queue">📨 Application queue</Link>
          <button onClick={reload}>🔄 Refresh</button>
        </div>
      </section>

      {health.loading && !health.result ? <Loading /> : (
        <div className="grid cols-2">
          {health.result?.ok ? <Alert kind="success">Backend: connected</Alert> : <Alert kind="error">Backend: unreachable<div className="muted">Start it with: <code>uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000</code></div></Alert>}
          {!health.result?.ok ? <Alert kind="warning">MongoDB: unknown (backend unreachable)</Alert> : db.result?.ok ? <Alert kind="success">MongoDB: connected</Alert> : <Alert kind="error">MongoDB: not connected</Alert>}
        </div>
      )}
      <p className="muted">Environment <b>{info.environment ?? "—"}</b> · version <b>{info.version ?? "—"}</b></p>

      <SearchNow onDone={() => summary.reload()} />

      {d && (
        <>
          <h2>At a glance</h2>
          <div className="home-stats">
            <Stat label="Jobs found" value={d.jobs.total} to="/job-discovery" help="Every job the app has saved" />
            {counts && <Stat label="Fit your preferences" value={counts.fit_preferences} to="/job-matching" help="Your titles and locations, plus jobs you imported" />}
            {counts
              ? <Stat label="Good matches" value={counts.good_matches} to="/job-matching" help={`Eligible (PASS) and score ≥ ${counts.good_score}, among jobs that fit your preferences`} />
              : <Stat label="Match your profile" value={d.jobs.matching_profile} to="/job-matching" help={`Fit your preferences, eligibility PASS and score ≥ ${d.min_match_score}`} />}
            <Stat label="Awaiting your approval" value={d.applications.awaiting_approval} to="/application-queue" />
            <Stat label="Submitted" value={d.applications.submitted} to="/applied-jobs" />
            <Stat label="Interviews this week" value={d.interviews.upcoming_7_days} to="/interview-tracker" />
          </div>
          {pendingTotal > 0 && <Alert kind="warning">👋 {pendingTotal} item(s) need your attention. <Link to="/application-queue">Open the application queue</Link></Alert>}
          {d.jobs.total === 0 && <Alert kind="info">No jobs yet. Start with <Link to="/job-discovery">Job Discovery</Link> to read company career pages, or import a link from LinkedIn or Naukri.</Alert>}
        </>
      )}

      <h2>How it works</h2>
      <ol className="home-steps">
        {WORKFLOW.map(([n, label, to]) => <li key={n}><Link to={to}><span className="home-step-n">{n}</span>{label}</Link></li>)}
      </ol>

      <h2>Where to go</h2>
      <div className="home-cards">
        {PAGES.filter((p) => p.path !== "/").map((p) => (
          <Link key={p.path} to={p.path} className="home-card">
            <span className="home-card-icon" aria-hidden="true">{p.icon}</span>
            <span><strong>{p.label}</strong><span className="muted home-card-text">{DESCRIPTIONS[p.path]}</span></span>
          </Link>
        ))}
      </div>
    </>
  );
}
