import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { AppliedSwitch, useApplicationStates } from "../components/AppliedSwitch";
import { PageHeader } from "../components/Layout";
import { LinkedInBulkPaste, LinkedInJobsTable, LinkedInQuickImport, LinkedInSearch, type LinkedInSuggestions } from "../components/LinkedInTools";
import { NaukriBulkPaste, NaukriQuickImport, NaukriSearch } from "../components/NaukriTools";
import { Alert, Check, DataTable, Empty, ErrorBox, Field, Loading, MetricRow, Paginator, Progress } from "../components/ui";
import { fmtDt } from "../format";
import { useFetch } from "../hooks";
import {
  blockedSources, buildDiscoverBody, buildImportBody, buildJobParams, bySource, defaultFilters, EMPLOYMENT_TYPES, exp, importMessage, jobRows, latestScores, money, runProgress,
  runSourceRows, SORTS, sourceLabel, sourceRows, WORK_MODES, type DiscoverForm, type JobFilters,
} from "../lib/discovery";

const safeUrl = (u: unknown): string | null => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);

/** Job Discovery: find, import and browse jobs from permitted sources. Sources that are manual-only/disabled cannot be selected for automated discovery. */
export default function JobDiscovery() {
  const sources = useFetch<any[]>("/api/jobs/sources");
  const [lastRun, setLastRun] = useState<any>(null);
  const status = useFetch<any>("/api/jobs/discovery/status");
  const runs = useFetch<any[]>("/api/jobs/discovery/runs", { limit: 10 });
  const liSuggest = useFetch<LinkedInSuggestions>("/api/jobs/linkedin/suggestions");
  const [liKey, setLiKey] = useState(0);
  const list = sources.data ?? [];

  return (
    <>
      <PageHeader icon="🔎" title="Job Discovery" />
      <h2>1. Sources</h2>
      {sources.loading && !sources.result && <Loading />}
      <ErrorBox result={sources.result} />
      {sources.result?.ok && <SourcesSection sources={list} />}
      {sources.result?.ok && <DiscoverSection sources={list} onRun={(r) => { setLastRun(r); status.reload(); runs.reload(); }} />}
      <LastRun run={lastRun ?? (status.result?.ok ? status.data : null)} />
      <h2>LinkedIn (manual)</h2>
      <LinkedInSearch suggestions={liSuggest.data} />
      <LinkedInBulkPaste onDone={() => { runs.reload(); setLiKey((k) => k + 1); }} />
      <LinkedInQuickImport onDone={() => { runs.reload(); setLiKey((k) => k + 1); }} />
      <LinkedInJobsTable reloadKey={liKey} />
      <h2>Naukri (manual)</h2>
      <NaukriSearch />
      <NaukriQuickImport onDone={() => runs.reload()} />
      <NaukriBulkPaste onDone={() => runs.reload()} />
      <ImportSection onDone={() => runs.reload()} />
      {sources.result?.ok && <JobsSection sources={list} />}
      <h2>4. Discovery history</h2>
      {runs.loading && !runs.result && <Loading />}
      <ErrorBox result={runs.result} />
      {runs.result?.ok && (!runs.data?.length ? <Empty message="No discovery run yet. Use 'Discover jobs' above (or the scheduler on the Automation page)." /> : (
        <DataTable rows={runs.data} columns={[
          { header: "Started", render: (r: any) => fmtDt(r.started_at) }, { header: "Status", render: (r: any) => r.status },
          { header: "Sources", render: (r: any) => (r.requested_sources ?? []).join(", ") || "—" }, { header: "Skipped", render: (r: any) => Object.keys(r.skipped_sources ?? {}).join(", ") || "—" },
          { header: "Fetched", render: (r: any) => r.totals?.fetched ?? 0 }, { header: "New", render: (r: any) => r.totals?.new ?? 0 },
          { header: "Duplicates", render: (r: any) => r.totals?.duplicates ?? 0 }, { header: "Flagged", render: (r: any) => r.totals?.flagged ?? 0 }, { header: "Error", render: (r: any) => r.error || "" },
        ]} />
      ))}
    </>
  );
}

function SourcesSection({ sources }: { sources: any[] }) {
  const blocked = blockedSources(sources);
  const cp = sources.find((s) => s.source === "career_pages");
  return (
    <>
      <DataTable rows={sourceRows(sources)} columns={[
        { header: "Source", render: (r) => r.source }, { header: "Status", render: (r) => r.status }, { header: "Automated discovery", render: (r) => r.automated },
        { header: "Manual import", render: (r) => r.manual }, { header: "Jobs stored", render: (r) => r.stored }, { header: "Why / notes", render: (r) => r.why },
      ]} />
      {blocked.length > 0 && (
        <details><summary>Why are some sources not available for automatic discovery?</summary>
          {blocked.map((s) => <p key={s.source}><b>{sourceLabel(s.source)}</b> ({s.status}): {s.basis || s.reason}</p>)}
          <p className="muted">Policy review is desk research (not legal advice). Statuses live in <code>config/platform_policies.yaml</code>; unknown sources are always treated as disabled.</p>
        </details>
      )}
      {cp?.automated_discovery && !cp.companies_configured && (
        <Alert>Company career pages are enabled but <b>no companies are configured</b>. Add companies (Greenhouse board token / Lever site / approved career page) to <code>config/career_sources.yaml</code> - nothing is discovered otherwise.</Alert>
      )}
    </>
  );
}

function DiscoverSection({ sources, onRun }: { sources: any[]; onRun: (run: any) => void }) {
  const runnable = sources.filter((s) => s.automated_discovery).map((s) => s.source as string);
  const [f, setF] = useState<DiscoverForm>({ usePrefs: true, titles: "", skills: "", locations: "", companies: "", modes: [], employment: [], minSalary: 0, expMax: 0, postedDays: 0, sources: runnable });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warn, setWarn] = useState<{ message: string; skipped: Record<string, string> } | null>(null);
  const [skipped, setSkipped] = useState<Record<string, string>>({});
  const [progress, setProgress] = useState<{ value: number; text: string; states: Record<string, string> } | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const toggle = (key: "modes" | "employment" | "sources", v: string) => setF((p) => ({ ...p, [key]: p[key].includes(v) ? p[key].filter((x) => x !== v) : [...p[key], v] }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null); setWarn(null); setSkipped({}); setProgress(null);
    const res = await api.post("/api/jobs/discover", { json: buildDiscoverBody(f) });
    if (!res.ok) { setError(res.error ?? "Failed"); setBusy(false); return; }
    const started = res.data;
    if (started.status === "nothing_to_run") { setWarn({ message: started.message, skipped: started.skipped_sources ?? {} }); setBusy(false); return; }
    setSkipped(started.skipped_sources ?? {});
    let run: any = {};
    for (let tick = 0; tick < 180 && alive.current; tick++) {                      // poll for up to ~3 minutes
      const st = await api.get("/api/jobs/discovery/status", { params: { run_id: started.run_id } });
      if (!st.ok) { run = {}; setError(st.error ?? "Failed"); break; }
      run = st.data;
      if (run.status !== "running") break;
      setProgress({ value: runProgress(run), text: "Discovering…", states: Object.fromEntries(Object.entries(run.sources ?? {}).map(([k, v]: [string, any]) => [k, v.state])) });
      await new Promise((r) => setTimeout(r, 1000));
    }
    if (!alive.current) return;
    setProgress({ value: 1, text: `Finished: ${run.status ?? "unknown"}`, states: {} });
    setBusy(false);
    if (run.status) onRun(run);
  }

  return (
    <>
      <h2>2. Discover jobs</h2>
      <form noValidate onSubmit={submit} aria-label="Discover jobs">
        <Check label="Start from my saved preferences (config/job_preferences.yaml)" checked={f.usePrefs} onChange={(v) => setF({ ...f, usePrefs: v })} />
        <div className="grid cols-2">
          <Field label="Job titles / keywords (one per line)"><textarea value={f.titles} onChange={(e) => setF({ ...f, titles: e.target.value })} /></Field>
          <Field label="Technical skills (one per line)"><textarea value={f.skills} onChange={(e) => setF({ ...f, skills: e.target.value })} /></Field>
        </div>
        <div className="grid cols-3">
          <Field label="Locations (comma separated)"><input value={f.locations} onChange={(e) => setF({ ...f, locations: e.target.value })} /></Field>
          <Field label="Companies (comma separated)"><input value={f.companies} onChange={(e) => setF({ ...f, companies: e.target.value })} /></Field>
          <fieldset><legend>Work mode</legend>{WORK_MODES.map((m) => <Check key={m} label={m} checked={f.modes.includes(m)} onChange={() => toggle("modes", m)} />)}</fieldset>
        </div>
        <div className="grid cols-4">
          <fieldset><legend>Employment type</legend>{EMPLOYMENT_TYPES.map((m) => <Check key={m} label={m} checked={f.employment.includes(m)} onChange={() => toggle("employment", m)} />)}</fieldset>
          <Field label="Minimum salary (annual)"><input type="number" min={0} step={100000} value={f.minSalary} onChange={(e) => setF({ ...f, minSalary: Number(e.target.value) })} /></Field>
          <Field label="My experience (years)"><input type="number" min={0} max={60} step={0.5} value={f.expMax} onChange={(e) => setF({ ...f, expMax: Number(e.target.value) })} /><span className="muted">Jobs that require more than this are left out. 0 = no filter.</span></Field>
          <Field label="Posted within (days)"><input type="number" min={0} max={365} value={f.postedDays} onChange={(e) => setF({ ...f, postedDays: Number(e.target.value) })} /><span className="muted">0 = any date; jobs without a date are kept</span></Field>
        </div>
        <fieldset><legend>Sources</legend>
          {runnable.length === 0 && <span className="muted">No source allows automated discovery.</span>}
          {runnable.map((s) => <Check key={s} label={sourceLabel(s)} checked={f.sources.includes(s)} onChange={() => toggle("sources", s)} />)}
        </fieldset>
        <button type="submit" className="primary" disabled={!runnable.length || busy}>Discover jobs</button>
      </form>
      {error && <Alert kind="error">{error}</Alert>}
      {warn && <Alert kind="warning">{warn.message}{Object.entries(warn.skipped).map(([s, why]) => <div key={s}>• <b>{s}</b>: {why}</div>)}</Alert>}
      {Object.entries(skipped).map(([s, why]) => <div key={s} className="muted">Skipped {s}: {why}</div>)}
      {progress && <><Progress value={progress.value} text={progress.text} /><pre>{JSON.stringify(progress.states)}</pre></>}
    </>
  );
}

function LastRun({ run }: { run: any }) {
  if (!run || !run.status || run.status === "never_run") return null;
  const t = run.totals ?? {};
  const rows = runSourceRows(run);
  return (
    <>
      <h3>Last discovery run</h3>
      <MetricRow items={[["Fetched", t.fetched ?? 0], ["New jobs", t.new ?? 0], ["Duplicates", t.duplicates ?? 0], ["Filtered out", t.filtered_out ?? 0], ["Flagged for review", t.flagged ?? 0], ["Invalid", t.invalid ?? 0]]} />
      <p className="muted">Status: <b>{run.status}</b> · started {String(run.started_at ?? "").slice(0, 19).replace("T", " ")}</p>
      {rows.length > 0 && <DataTable rows={rows} columns={[
        { header: "Source", render: (r) => r.source }, { header: "State", render: (r) => r.state }, { header: "Fetched", render: (r) => r.fetched }, { header: "New", render: (r) => r.new },
        { header: "Updated", render: (r) => r.updated }, { header: "Unchanged", render: (r) => r.unchanged }, { header: "Merged", render: (r) => r.merged }, { header: "Flagged", render: (r) => r.flagged },
        { header: "Filtered", render: (r) => r.filtered }, { header: "Requests", render: (r) => r.requests }, { header: "Problems", render: (r) => r.problems },
      ]} />}
      {Object.entries(run.skipped_sources ?? {}).map(([s, why]) => <div key={s} className="muted">⏭️ {s}: {String(why)}</div>)}
    </>
  );
}

function ImportSection({ onDone }: { onDone: () => void }) {
  const blank = { url: "", title: "", company: "", loc: "", desc: "", emp: "", sal: "", date: "" };
  const [v, setV] = useState(blank);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof blank) => (e: { target: { value: string } }) => setV({ ...v, [k]: e.target.value });
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setMsg(null);
    const res = await api.post("/api/jobs/import", { json: buildImportBody(v.url, { job_title: v.title, company_name: v.company, location: v.loc, job_description: v.desc, employment_type: v.emp, salary: v.sal, posted_date: v.date }) });
    setBusy(false);
    if (!res.ok) setMsg({ ok: false, text: res.error ?? "Failed" });
    else { setMsg({ ok: true, text: importMessage(res.data.action, res.data.match) }); onDone(); }
  }
  return (
    <details><summary>➕ Import a job manually (LinkedIn / Indeed / Naukri / Instahyre / anywhere)</summary>
      <p className="muted">For platforms that do not allow automated collection: copy the job details yourself. The URL is stored but never requested. (A public Greenhouse or Lever job URL is completed through their official public API.)</p>
      <form onSubmit={submit} aria-label="Import a job">
        <Field label="Job URL"><input value={v.url} onChange={set("url")} /></Field>
        <div className="grid cols-3">
          <Field label="Job title"><input value={v.title} onChange={set("title")} /></Field>
          <Field label="Company"><input value={v.company} onChange={set("company")} /></Field>
          <Field label="Location"><input value={v.loc} onChange={set("loc")} /></Field>
        </div>
        <Field label="Job description"><textarea style={{ minHeight: 160 }} value={v.desc} onChange={set("desc")} /></Field>
        <div className="grid cols-3">
          <Field label="Employment type"><input value={v.emp} onChange={set("emp")} /></Field>
          <Field label="Salary (e.g. INR 20-30 LPA)"><input value={v.sal} onChange={set("sal")} /></Field>
          <Field label="Date posted (e.g. 2026-11-05)"><input value={v.date} onChange={set("date")} /></Field>
        </div>
        <button type="submit" disabled={busy}>Import job</button>
      </form>
      {msg && <Alert kind={msg.ok ? "success" : "error"}>{msg.text}</Alert>}
    </details>
  );
}

function JobsSection({ sources }: { sources: any[] }) {
  const [f, setF] = useState<JobFilters>(defaultFilters());
  const [size, setSize] = useState(25);
  const [page, setPage] = useState(1);
  const params = buildJobParams(f);
  const probe = useFetch<any>("/api/jobs/page", { ...params, limit: 1 });
  const total: number = probe.data?.total ?? 0;
  const skip = (Math.min(page, Math.max(1, Math.ceil(total / size))) - 1) * size;
  const pg = useFetch<any>(total ? "/api/jobs/page" : null, { ...params, limit: size, skip });
  const scores = useFetch<any>("/api/matching/results", { limit: 200 });
  const jobs: any[] = pg.data?.items ?? [];
  const [pick, setPick] = useState("");
  useEffect(() => { if (jobs.length && !jobs.some((j) => j.job_id === pick)) setPick(jobs[0].job_id); }, [jobs, pick]);
  const update = (patch: Partial<JobFilters>) => { setF({ ...f, ...patch }); setPage(1); };
  const scoreOf = latestScores(scores.result?.ok ? scores.data : null);
  const rows = jobRows(jobs, scoreOf);
  const applied = useApplicationStates(jobs.map((j) => j.job_id));
  const [applyErr, setApplyErr] = useState<string | null>(null);
  const counts = Object.entries(bySource(jobs)).map(([k, v]) => `${sourceLabel(k)} ${v}`).join(", ");

  return (
    <>
      <h2>3. Discovered jobs</h2>
      <div className="grid cols-4">
        <Field label="Title contains"><input value={f.title} onChange={(e) => update({ title: e.target.value })} /></Field>
        <Field label="Company contains"><input value={f.company} onChange={(e) => update({ company: e.target.value })} /></Field>
        <Field label="Location contains"><input value={f.location} onChange={(e) => update({ location: e.target.value })} /></Field>
        <Field label="Source"><select value={f.source} onChange={(e) => update({ source: e.target.value })}><option value="All">All sources</option>{sources.map((s) => <option key={s.source} value={s.source}>{sourceLabel(s.source)}</option>)}</select></Field>
      </div>
      <div className="grid cols-4">
        <Field label="Max experience required (years)"><input type="number" min={0} max={60} step={0.5} value={f.maxExp} onChange={(e) => update({ maxExp: Number(e.target.value) })} /><span className="muted">0 = no filter</span></Field>
        <Field label="Min match score"><input type="number" min={0} max={100} value={f.minScore} onChange={(e) => update({ minScore: Number(e.target.value) })} /><span className="muted">Latest saved match score for the job; 0 = no filter</span></Field>
        <Field label="Sort by"><select value={f.sortLabel} onChange={(e) => update({ sortLabel: e.target.value })}>{Object.keys(SORTS).map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Check label="Newest / highest first" checked={f.desc} onChange={(v) => update({ desc: v })} />
      </div>
      <div className="grid cols-4">
        <Field label="Discovered from"><input type="date" value={f.from} onChange={(e) => update({ from: e.target.value })} /></Field>
        <Field label="Discovered to"><input type="date" value={f.to} onChange={(e) => update({ to: e.target.value })} /></Field>
        <Check label="Only possible duplicates (needs review)" checked={f.reviewOnly} onChange={(v) => update({ reviewOnly: v })} />
        <Check label="Only jobs that fit my preferences" checked={f.preferredOnly} onChange={(v) => update({ preferredOnly: v })} />
        <Field label="Per page"><select value={size} onChange={(e) => { setSize(Number(e.target.value)); setPage(1); }}>{[10, 25, 50].map((n) => <option key={n}>{n}</option>)}</select></Field>
      </div>
      {probe.loading && !probe.result && <Loading />}
      <ErrorBox result={probe.result} />
      {probe.result?.ok && !total && <Empty message="No jobs match. Run discovery or import a job above, or loosen the filters." />}
      {total > 0 && (
        <>
          <Paginator total={total} size={size} page={page} onPage={setPage} />
          <ErrorBox result={pg.result} />
          {pg.loading && !pg.result && <Loading />}
          {jobs.length > 0 && <p className="muted">{jobs.length} of {total} job(s) shown · this page by source: {counts}</p>}
          {applyErr && <Alert kind="error">{applyErr}</Alert>}
          <DataTable rows={rows} rowKey={(r) => r.id} columns={[
            { header: "Applied", render: (r) => <AppliedSwitch jobId={r.id} title={r.title} company={r.company} state={applied.states[r.id]}
                onRecorded={() => { setApplyErr(null); applied.reload(); }} onError={setApplyErr} /> },
            { header: "Title", render: (r) => r.title }, { header: "Company", render: (r) => r.company }, { header: "Location", render: (r) => r.location }, { header: "Match", render: (r) => r.match ?? "" },
            { header: "Link", render: (r) => r.url ? <a href={r.url} target="_blank" rel="noopener noreferrer">Open job ↗</a> : "—" },
            { header: "Mode", render: (r) => r.mode }, { header: "Experience", render: (r) => r.experience }, { header: "Salary", render: (r) => r.salary }, { header: "Posted", render: (r) => r.posted },
            { header: "Discovered", render: (r) => r.discovered }, { header: "Sources", render: (r) => r.sources }, { header: "Status", render: (r) => r.status }, { header: "Duplicate check", render: (r) => r.duplicate },
          ]} />
          {jobs.length > 0 && (
            <>
              <Field label="Open a job"><select value={pick} onChange={(e) => setPick(e.target.value)}>{jobs.map((j) => <option key={j.job_id} value={j.job_id}>{j.job_title || "(unknown)"} - {j.company_name || "(unknown)"}</option>)}</select></Field>
              {pick && <JobDetail id={pick} key={pick} />}
            </>
          )}
        </>
      )}
    </>
  );
}

function JobDetail({ id }: { id: string }) {
  const d = useFetch<any>(`/api/jobs/${encodeURIComponent(id)}`);
  const [refresh, setRefresh] = useState<{ ok: boolean; text: string } | null>(null);
  if (d.loading && !d.result) return <Loading />;
  if (!d.result?.ok) return <ErrorBox result={d.result} />;
  const job = d.data;
  const url = safeUrl(job.job_url);
  const fromCareerPage = (job.sources ?? []).some((s: any) => s.source === "company_career_page");
  async function doRefresh() {
    const r = await api.post(`/api/jobs/${encodeURIComponent(id)}/refresh`);
    setRefresh(r.ok ? { ok: true, text: `Refresh result: ${r.data.action}` } : { ok: false, text: r.error ?? "Failed" });
  }
  return (
    <>
      <h3>{job.job_title || "(unknown title)"} - {job.company_name || "(unknown company)"}</h3>
      <MetricRow items={[["Location", (job.location ?? []).join(", ") || "—"], ["Work mode", job.work_mode || "—"], ["Experience", exp(job)], ["Salary", money(job)]]} />
      <p className="muted">Employment: {job.employment_type || "—"} · Posted: {(job.posted_date || "—").slice(0, 10)} · Discovered: {String(job.discovered_at).slice(0, 10)} · Last updated: {String(job.last_updated_at).slice(0, 10)} · Status: {job.status}</p>
      {url && <a href={url} target="_blank" rel="noopener noreferrer">Open original posting</a>}
      {(job.needs_review || job.dedup_status !== "unique") && <Alert kind="warning">Duplicate check: <b>{job.dedup_status}</b> (confidence {job.dedup_confidence}): {(job.dedup_reasons ?? []).join("; ")}</Alert>}
      <p><b>Source references:</b> {(job.sources ?? []).map((s: any) => `${s.source}${s.provider ? "/" + s.provider : ""}`).join(" · ")}</p>
      <p><b>Required skills:</b> {(job.mandatory_skills ?? []).join(", ") || "—"}<br /><b>Preferred skills:</b> {(job.preferred_skills ?? []).join(", ") || "—"}</p>
      <Field label="Description (plain text preview)"><textarea readOnly style={{ minHeight: 220 }} value={(job.job_description || "No description available.").slice(0, 3000)} /></Field>
      {job.warnings?.length > 0 && <p className="muted">Data notes: {job.warnings.join("; ")}</p>}
      {fromCareerPage ? <button onClick={doRefresh}>🔄 Refresh details from source</button> : <p className="muted">Refresh is only available for jobs from permitted automated sources. Re-import manual jobs to update them.</p>}
      {refresh && <Alert kind={refresh.ok ? "success" : "error"}>{refresh.text}</Alert>}
    </>
  );
}
