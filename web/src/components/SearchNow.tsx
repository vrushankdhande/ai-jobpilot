import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useFetch } from "../hooks";
import { buildDiscoverBody } from "../lib/discovery";
import { Alert, Field, Progress } from "./ui";

/** One-time job search: runs discovery once, right now, on the sources the policy allows (company career pages). It is not scheduled and repeats nothing. */
export default function SearchNow({ onDone }: { onDone?: () => void }) {
  const sources = useFetch<any[]>("/api/jobs/sources");
  const runnable = (sources.data ?? []).filter((s) => s.automated_discovery).map((s) => s.source as string);
  const [titles, setTitles] = useState("");
  const [locations, setLocations] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [result, setResult] = useState<any>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null); setResult(null); setProgress(0.05);
    const body = buildDiscoverBody({ usePrefs: true, titles, skills: "", locations, companies: "", modes: [], employment: [], minSalary: 0, expMax: 0, postedDays: 0, sources: runnable });
    const res = await api.post("/api/jobs/discover", { json: body });
    if (!res.ok) { setError(res.error ?? "The search could not start"); setBusy(false); setProgress(null); return; }
    if (res.data.status === "nothing_to_run") { setError(res.data.message ?? "No source is available for automatic search."); setBusy(false); setProgress(null); return; }
    let run: any = {};
    for (let tick = 0; tick < 180 && alive.current; tick++) {
      const st = await api.get("/api/jobs/discovery/status", { params: { run_id: res.data.run_id } });
      if (!st.ok) { setError(st.error ?? "Failed"); break; }
      run = st.data;
      if (run.status !== "running") break;
      setProgress(Math.min(0.95, 0.1 + tick / 120));
      await new Promise((r) => setTimeout(r, 1000));
    }
    if (!alive.current) return;
    setBusy(false); setProgress(null);
    if (run.status && run.status !== "running") { setResult(run); onDone?.(); }
    else if (run.status === "running") setError("The search is still running. Check Job Discovery in a minute.");
  }

  const t = result?.totals ?? {};
  return (
    <section className="search-now" aria-label="Search once now">
      <h2>🔎 Search once, now</h2>
      <p className="muted">Runs a single search of the company career pages right away. It is not scheduled and does not repeat. Leave the boxes empty to use your saved preferences.</p>
      <form noValidate onSubmit={submit} className="row">
        <Field label="Job title / keywords (optional)"><input value={titles} onChange={(e) => setTitles(e.target.value)} placeholder="e.g. machine learning engineer" /></Field>
        <Field label="Location (optional)"><input value={locations} onChange={(e) => setLocations(e.target.value)} placeholder="e.g. remote, Pune" /></Field>
        <button type="submit" className="primary" disabled={busy || sources.loading || runnable.length === 0}>{busy ? "Searching…" : "Search now"}</button>
      </form>
      {runnable.length === 0 && sources.result?.ok && <Alert kind="warning">No source allows automatic search right now. Import jobs by link in Job Discovery.</Alert>}
      {progress !== null && <Progress value={progress} text="Searching company career pages…" />}
      {error && <Alert kind="error">{error}</Alert>}
      {result && (
        <Alert kind={result.status === "completed" || result.status === "success" ? "success" : "warning"}>
          Search finished ({result.status}): <b>{t.new ?? 0}</b> new job(s), {t.fetched ?? 0} checked, {t.duplicates ?? 0} already stored.{" "}
          <Link to="/job-discovery">See the jobs</Link> · <Link to="/job-matching">Score them</Link>
        </Alert>
      )}
    </section>
  );
}
