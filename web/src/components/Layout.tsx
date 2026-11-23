import { useEffect } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { schedulerState } from "../format";
import { useFetch, useWebConfig } from "../hooks";
import { FlashBanner } from "./ui";

export const PAGES: Array<{ path: string; label: string; icon: string }> = [
  { path: "/", label: "Home", icon: "🧭" },
  { path: "/dashboard", label: "Dashboard", icon: "📊" },
  { path: "/job-discovery", label: "Job Discovery", icon: "🔎" },
  { path: "/resume-manager", label: "Resume Manager", icon: "📄" },
  { path: "/job-matching", label: "Job Matching", icon: "🎯" },
  { path: "/document-generation", label: "Document Generation", icon: "📝" },
  { path: "/application-queue", label: "Application Queue", icon: "📬" },
  { path: "/applied-jobs", label: "Applied Jobs", icon: "🗂️" },
  { path: "/interview-tracker", label: "Interview Tracker", icon: "🗓️" },
  { path: "/analytics", label: "Analytics", icon: "📈" },
  { path: "/automation", label: "Automation", icon: "🤖" },
  { path: "/settings", label: "Settings", icon: "⚙️" },
];

export function PageHeader({ icon, title }: { icon: string; title: string }) {
  useEffect(() => { document.title = `${title} · AI JobPilot`; }, [title]);
  return <h1>{icon} {title}</h1>;
}

export default function Layout() {
  const health = useFetch("/health");
  const sched = useFetch(health.data ? "/api/scheduler/status" : null);
  const cfg = useWebConfig();
  const up = Boolean(health.result?.ok);
  return (
    <div className="shell">
      <aside className="sidebar" aria-label="Sidebar">
        <h3>🧭 AI JobPilot</h3>
        <nav aria-label="App pages">
          {PAGES.map((p) => <NavLink key={p.path} to={p.path} end={p.path === "/"}>{p.icon} {p.label}</NavLink>)}
        </nav>
        <div className="side-status">
          {health.loading && !health.result ? <div className="muted">Checking backend…</div>
            : up ? <div className="alert success">✅ Backend connected</div>
              : <div className="alert error">🛑 Backend unreachable<div className="muted">Start it: <code>uv run uvicorn backend.main:app --port 8000</code></div></div>}
          {sched.data && <div className="muted">Scheduler: {schedulerState(sched.data)} · {sched.data.timezone}</div>}
          <div className="muted">Admin controls: {cfg.adminEnabled ? "enabled" : "read-only (set ADMIN_TOKEN)"}</div>
        </div>
      </aside>
      <main className="content"><FlashBanner /><Outlet /></main>
    </div>
  );
}
