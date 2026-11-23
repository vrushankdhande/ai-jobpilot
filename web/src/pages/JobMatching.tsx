import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { AppliedSwitch } from "../components/AppliedSwitch";
import { PageHeader } from "../components/Layout";
import { Alert, Check, DataTable, Empty, ErrorBox, Field, Loading, Metric, MetricRow, Progress } from "../components/ui";
import { useFetch } from "../hooks";
import { httpUrl } from "../lib/discovery";
import {
  APPLIED_LABELS, ELIGIBILITY_FILTERS, HISTORY_PAGE_SIZE, NO_FILTERS, RECOMMENDATIONS, SORT_LABELS, categoryLabel, categoryProgress, clampScore, completeness, decodeJobDescription, eligLabel, expLabel, g, historyParams, pct,
  rangeProblem, recommendationKind, roleFitLabel, scoreBody, scoreLabel, type HistoryFilters,
} from "../lib/matching";

/** Job Matching: the numeric score and the eligibility result are computed deterministically by the backend; this page only displays them. */
export default function JobMatching() {
  const cfg = useFetch<any>("/api/matching/thresholds");
  const resumes = useFetch<any[]>("/api/resumes");
  const active = resumes.data?.find((r) => r.is_active) ?? null;
  const profile = useFetch<any>(active ? `/api/resumes/${active.resume_id}/profile` : null);
  const [report, setReport] = useState<any | null>(null);
  const [allowUnreviewed, setAllowUnreviewed] = useState(false);

  return (
    <>
      <PageHeader icon="🎯" title="Job Matching" />
      {(cfg.loading && !cfg.result) || (resumes.loading && !resumes.result) ? <Loading /> : null}
      <ErrorBox result={cfg.result} />
      <ErrorBox result={cfg.result?.ok ? resumes.result : null} />
      {cfg.data && resumes.data && !active && <Alert kind="warning">No active resume. Upload one in <Link to="/resume-manager">Resume Manager</Link> and make it active first.</Alert>}
      {cfg.data && active && profile.loading && !profile.result && <Loading />}
      {cfg.data && active && profile.result && !profile.result.ok && <Alert kind="error">The active resume has no usable profile: {profile.result.error}</Alert>}
      {cfg.data && active && profile.data && (
        <Body cfg={cfg.data} active={active} profile={profile.data} report={report} setReport={setReport} allowUnreviewed={allowUnreviewed} setAllowUnreviewed={setAllowUnreviewed} />
      )}
    </>
  );
}

function Body({ cfg, active, profile, report, setReport, allowUnreviewed, setAllowUnreviewed }:
  { cfg: any; active: any; profile: any; report: any; setReport: (r: any) => void; allowUnreviewed: boolean; setAllowUnreviewed: (v: boolean) => void }) {
  const [fromTable, setFromTable] = useState(false);
  const ai = cfg.ai;
  const reviewed = profile.review_status === "reviewed";
  const missing: string[] = profile.missing_fields ?? [];
  return (
    <>
      <h2>A. Candidate profile</h2>
      <div className="grid cols-4">
        <Metric label="Active resume" value={String(active.original_filename).slice(0, 28)} />
        <Metric label="Review status" value={String(profile.review_status).replaceAll("_", " ")} />
        <Metric label="Profile version" value={profile.profile_version} />
        <Metric label="Completeness" value={pct(completeness(missing.length))} help="Share of the 18 key profile fields that are filled in" />
      </div>
      {!reviewed && (
        <>
          <Alert kind="warning">This profile has <b>not been reviewed</b>. Matching uses the candidate&apos;s verified data - review and correct it in Resume Manager first. You can still run a match on the unreviewed profile after confirming below.</Alert>
          <Check label="I understand and want to match against the unreviewed profile" checked={allowUnreviewed} onChange={setAllowUnreviewed} />
        </>
      )}
      {missing.length > 0 && <details><summary>Missing profile information ({missing.length})</summary>{missing.join(", ")}</details>}
      <p className="muted">Scoring version <b>{cfg.scoring_version}</b> · recommendation threshold <b>{g(cfg.recommendation_threshold)}</b> · {ai.enabled ? "🤖 Claude analysis available" : `⚙️ Deterministic mode (${ai.reason})`}</p>

      <h2>B. Job description</h2>
      <JobForm ai={ai} canRun={reviewed || allowUnreviewed} allowUnreviewed={allowUnreviewed} onReport={(r) => { setFromTable(false); setReport(r); }} />

      {/* A report opened from the results table is shown UNDER the table, so the page does not jump away from the row you clicked. */}
      {!fromTable && <div id="match-report">{report && <Results rep={report} threshold={cfg.recommendation_threshold} allowUnreviewed={allowUnreviewed} onReport={setReport} />}</div>}
      <History onOpen={(r) => { setFromTable(true); setReport(r); }} />
      {fromTable && report && (
        <div id="match-report">
          <p><button onClick={() => { setReport(null); document.getElementById("match-results")?.scrollIntoView?.({ block: "start" }); }}>✖ Close report and go back to the list</button></p>
          <Results rep={report} threshold={cfg.recommendation_threshold} allowUnreviewed={allowUnreviewed} onReport={setReport} />
        </div>
      )}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ B. job description input
function JobForm({ ai, canRun, allowUnreviewed, onReport }: { ai: any; canRun: boolean; allowUnreviewed: boolean; onReport: (r: any) => void }) {
  const [v, setV] = useState({ text: "", title: "", company: "", location: "", url: "", experience: "", salary: "" });
  const [useAi, setUseAi] = useState(Boolean(ai.enabled));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [lastUpload, setLastUpload] = useState("");
  const set = (k: keyof typeof v, val: string) => setV((cur) => ({ ...cur, [k]: val }));

  const upload = async (f: File | null) => {
    if (!f) return;
    const sig = `${f.name}:${f.size}`;
    if (sig === lastUpload) return;
    setLastUpload(sig);
    try { set("text", decodeJobDescription(await f.arrayBuffer())); setErr(null); } catch { setErr("That file is not UTF-8 text."); }
  };
  const run = async () => {
    setBusy(true); setErr(null); setNote(null);
    const job = { job_description: v.text, job_title: v.title, company_name: v.company, location: v.location, job_url: v.url, experience_requirement: v.experience, salary: v.salary };
    const r = await api.post("/api/matching/score", { json: scoreBody(job, allowUnreviewed, useAi, Boolean(ai.enabled)), timeoutMs: 60_000 });
    setBusy(false);
    if (!r.ok) { setErr(r.error ?? "Failed"); return; }
    onReport(r.data.report);
    if (!r.data.created) setNote("Identical inputs were already scored; showing the saved result.");
  };

  return (
    <>
      <Field label="…or upload the job description as a text file (.txt / .md)"><input type="file" accept=".txt,.md" aria-label="Upload job description file" onChange={(e) => void upload(e.target.files?.[0] ?? null)} /></Field>
      <Field label="Paste the job description"><textarea style={{ height: 240 }} value={v.text} onChange={(e) => set("text", e.target.value)} /></Field>
      <div className="grid cols-3">
        <Field label="Job title"><input value={v.title} onChange={(e) => set("title", e.target.value)} /></Field>
        <Field label="Company"><input value={v.company} onChange={(e) => set("company", e.target.value)} /></Field>
        <Field label="Location"><input value={v.location} onChange={(e) => set("location", e.target.value)} /></Field>
        <Field label="Job URL"><input value={v.url} onChange={(e) => set("url", e.target.value)} /></Field>
        <Field label="Experience requirement (optional)"><input placeholder="e.g. 3-5 years" value={v.experience} onChange={(e) => set("experience", e.target.value)} /></Field>
        <Field label="Salary (optional)"><input placeholder="e.g. INR 20-30 LPA" value={v.salary} onChange={(e) => set("salary", e.target.value)} /></Field>
      </div>
      <Check label="Use Claude to refine the job analysis and wording" checked={ai.enabled ? useAi : false} onChange={setUseAi} disabled={!ai.enabled} />
      <p className="muted">Scores and eligibility are always computed by deterministic rules.</p>
      <button className="primary" disabled={!canRun || busy} onClick={() => void run()}>Analyze &amp; match</button>
      {busy && <Loading text="Analyzing…" />}
      {err && <Alert kind="error">{err}</Alert>}
      {note && <Alert>{note}</Alert>}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ C-F. results
function List({ items, empty = "None" }: { items: string[] | null | undefined; empty?: string }) {
  return <>{items?.length ? items.join(", ") : empty}</>;
}

function Results({ rep, threshold, allowUnreviewed, onReport }: { rep: any; threshold: number; allowUnreviewed: boolean; onReport: (r: any) => void }) {
  const elig = rep.eligibility;
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const rerun = async () => {
    setBusy(true); setErr(null);
    const r = await api.post(`/api/matching/${rep.match_id}/rerun`, { params: { allow_unreviewed: allowUnreviewed } });
    setBusy(false);
    if (r.ok) onReport(r.data); else setErr(r.error ?? "Failed");
  };
  return (
    <>
      <h2>C. Match score</h2>
      {(rep.job_title || rep.company_name) && <p className="muted">Report for <b>{rep.job_title || "(untitled job)"}</b>{rep.company_name ? <> at <b>{rep.company_name}</b></> : null}</p>}
      <div className="grid cols-4">
        <Metric label="Match score" value={`${g(rep.match_score)} / 100`} />
        <Metric label="Classification" value={rep.classification} />
        <Metric label="Eligibility" value={eligLabel(elig.status)} />
        <Metric label="Data coverage" value={`${g(rep.data_coverage)}%`} />
      </div>
      <Progress value={Math.min(1, rep.match_score / 100)} text={`Recommendation threshold: ${g(threshold)}`} />
      <Alert kind={recommendationKind(rep.recommendation_code)}>{rep.recommendation}</Alert>
      <p className="muted">The match score and eligibility are separate: a high score never overrides a failed mandatory requirement. {rep.jd_parser !== "deterministic" ? "Parsed by Claude + rules." : "Parsed by deterministic rules only (Claude was not used for parsing)."}</p>

      <h2>D. Score breakdown</h2>
      {rep.category_details.map((cat: any) => {
        const pr = categoryProgress(cat);
        return <div className="grid" style={{ gridTemplateColumns: "1fr 3fr" }} key={cat.name}><b>{categoryLabel(cat.name)}</b><Progress value={pr.ratio} text={pr.text} /></div>;
      })}
      <details><summary>How each category was scored</summary>
        {rep.category_details.map((cat: any) => (
          <div key={cat.name}><p><b>{categoryLabel(cat.name)}</b> - {(cat.notes ?? []).join("; ")}</p>
            {(cat.evidence ?? []).map((ev: any, i: number) => <p key={i} className="muted">JD: {ev.jd || "-"} ↔ Candidate: {ev.candidate || "-"}</p>)}</div>
        ))}
      </details>

      <h2>E. Skill comparison</h2>
      <div className="grid cols-3">
        <div><h3>✅ Matched</h3><List items={rep.matched_skills} /></div>
        <div><h3>❌ Missing mandatory</h3><List items={rep.missing_mandatory_skills} /></div>
        <div><h3>➖ Missing preferred</h3><List items={rep.missing_preferred_skills} /></div>
      </div>
      {rep.partial_matches?.length > 0 && (
        <>
          <h3>🔶 Partial matches (related, not equivalent)</h3>
          <DataTable rows={rep.partial_matches} columns={[{ header: "Candidate has", render: (p: any) => p.candidate_skill }, { header: "Job wants", render: (p: any) => p.required_skill }, { header: "Credit", render: (p: any) => p.credit }, { header: "Why it is not a full match", render: (p: any) => p.reason }]} />
        </>
      )}
      {rep.irrelevant_candidate_skills?.length > 0 && <p className="muted">Skills in the profile not relevant to this job: {rep.irrelevant_candidate_skills.join(", ")}</p>}
      <p className="muted">Never add a skill to your resume unless you genuinely have it.</p>

      <h3>Eligibility checks</h3>
      <DataTable rows={elig.checks} columns={[
        { header: "Check", render: (c: any) => c.name }, { header: "Result", render: (c: any) => eligLabel(c.status) }, { header: "Hard", render: (c: any) => (c.hard ? "yes" : "preference") },
        { header: "Requirement", render: (c: any) => c.requirement || "" }, { header: "Candidate", render: (c: any) => c.candidate_value || "" }, { header: "Detail", render: (c: any) => c.detail },
      ]} />

      <h2>F. Match explanation</h2>
      <p>{rep.explanation}</p>
      {rep.ai_summary && <Alert>🤖 AI-written summary (wording only, not used in scoring): {rep.ai_summary}</Alert>}
      <div className="grid cols-2">
        <div><h3>Why it matches</h3>{(rep.strengths?.length ? rep.strengths : ["Nothing notable."]).map((s: string, i: number) => <div key={i}>• {s}</div>)}</div>
        <div><h3>Why it may not match</h3>{(rep.weaknesses?.length ? rep.weaknesses : ["Nothing notable."]).map((s: string, i: number) => <div key={i}>• {s}</div>)}</div>
      </div>
      {rep.rejection_reasons?.length > 0 && <Alert kind="error"><b>Reasons for rejection / unresolved:</b><ul>{rep.rejection_reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul></Alert>}
      <details><summary>Experience, projects, responsibilities, education, location</summary>
        {([["Experience", rep.experience_analysis], ["Responsibilities", rep.responsibility_analysis], ["Education", rep.education_analysis], ["Location", rep.location_analysis]] as Array<[string, string[]]>).map(([t, items]) => (
          <div key={t}><p><b>{t}</b></p>{(items?.length ? items : ["-"]).map((i, k) => <div key={k}>• {i}</div>)}</div>
        ))}
        <p><b>Relevant projects</b></p>
        {(rep.project_relevance ?? []).map((p: any, i: number) => <div key={i}>• {p.project} - relevance {pct(p.relevance)} ({p.matched_skills?.length ? p.matched_skills.join(", ") : "related work"})</div>)}
      </details>
      <details><summary>Limitations of this result</summary>{(rep.limitations ?? []).map((l: string, i: number) => <div key={i}>• {l}</div>)}</details>
      <p className="muted">Match <code>{rep.match_id}</code> · scoring v{rep.scoring_version} · config <code>{rep.config_hash}</code></p>
      <button disabled={busy} onClick={() => void rerun()}>🔄 Re-run with current profile &amp; configuration</button>
      {busy && <Loading text="Re-running…" />}
      {err && <Alert kind="error">{err}</Alert>}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ G. match results
function History({ onOpen }: { onOpen: (rep: any) => void }) {
  const [f, setF] = useState<HistoryFilters>(NO_FILTERS);
  const [page, setPage] = useState(1);
  const [pickRaw, setPick] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const res = useFetch<any[]>("/api/matching/results", historyParams(f, page));
  const { limit: _l, skip: _s, sort: _o, ...countParams } = historyParams(f, 1);
  const total = useFetch<{ total: number }>("/api/matching/results/count", countParams).data?.total;
  const countsF = useFetch<any>("/api/matching/counts");
  const counts = countsF.data;
  const change = <K extends keyof HistoryFilters>(k: K, v: HistoryFilters[K]) => { setF((cur) => ({ ...cur, [k]: v })); setPage(1); };
  const filtered = JSON.stringify(f) !== JSON.stringify(NO_FILTERS);

  const all = res.data ?? [];
  const hasNext = all.length > HISTORY_PAGE_SIZE;
  const rows = all.slice(0, HISTORY_PAGE_SIZE);
  const pick = rows.some((r) => r.match_id === pickRaw) ? pickRaw : rows[0]?.match_id ?? "";

  const openId = async (id: string) => {
    setErr(null);
    const r = await api.get(`/api/matching/${id}`);
    if (r.ok) {
      onOpen(r.data);                                                             // the report renders under this table: bring it into view once it is on the page
      setTimeout(() => document.getElementById("match-report")?.scrollIntoView?.({ block: "start", behavior: "smooth" }), 60);
    } else setErr(r.error ?? "Failed");
  };

  return (
    <>
      <h2 id="match-results">G. Match results</h2>
      {counts && <MetricRow items={[
        ["Jobs stored", counts.jobs_stored, "Every job the app has saved"],
        ["Fit my preferences", counts.fit_preferences, "Your titles and locations, plus jobs you imported yourself"],
        ["Eligible (PASS)", counts.eligible, "Of the jobs that fit your preferences"],
        [`Good matches (PASS, ${counts.good_score}+)`, counts.good_matches, "Eligible, with a score at or above your bar"],
        ["Applied", counts.applied, "Jobs you marked as applied (or that were submitted)"],
        ["Still to apply", counts.to_apply, `Good matches you have not applied to yet (${counts.eligible_not_applied} eligible jobs in total not applied)`],
      ]} />}
      <p className="muted">One row per job (its newest score). Score is the fit with your profile; Eligibility is the separate check of the job's hard requirements. Use the filters to narrow the list.</p>
      <div className="grid cols-4">
        <Field label="Min score"><input type="number" min={0} max={100} value={f.minScore} onChange={(e) => change("minScore", clampScore(Number(e.target.value)))} /></Field>
        <Field label="Max score"><input type="number" min={0} max={100} value={f.maxScore} onChange={(e) => change("maxScore", clampScore(Number(e.target.value)))} /></Field>
        <Field label="Eligibility"><select value={f.eligibility} onChange={(e) => change("eligibility", e.target.value)}>{["Any", ...ELIGIBILITY_FILTERS].map((o) => <option key={o}>{o}</option>)}</select></Field>
        <Field label="Max years of experience required"><input type="number" min={0} max={60} step={0.5} value={f.maxExperience} onChange={(e) => change("maxExperience", Math.max(0, Number(e.target.value) || 0))} /><span className="muted">0 = any. Jobs that state no minimum are always shown.</span></Field>
        <Field label="Job title contains"><input value={f.title} onChange={(e) => change("title", e.target.value)} /></Field>
        <Field label="Company contains"><input value={f.company} onChange={(e) => change("company", e.target.value)} /></Field>
        <Field label="Recommendation"><select value={f.recommendation} onChange={(e) => change("recommendation", e.target.value)}>{["Any", ...RECOMMENDATIONS].map((o) => <option key={o}>{o}</option>)}</select></Field>
        <Field label="Application"><select value={f.applied} onChange={(e) => change("applied", e.target.value as HistoryFilters["applied"])}>{(Object.keys(APPLIED_LABELS) as Array<HistoryFilters["applied"]>).map((k) => <option key={k} value={k}>{APPLIED_LABELS[k]}</option>)}</select></Field>
        <Field label="Sort by"><select value={f.sort} onChange={(e) => change("sort", e.target.value as HistoryFilters["sort"])}>{(Object.keys(SORT_LABELS) as Array<HistoryFilters["sort"]>).map((k) => <option key={k} value={k}>{SORT_LABELS[k]}</option>)}</select></Field>
      </div>
      <div className="grid cols-2">
        <Field label="From date"><input type="date" value={f.dateFrom} onChange={(e) => change("dateFrom", e.target.value)} /></Field>
        <Field label="To date"><input type="date" value={f.dateTo} onChange={(e) => change("dateTo", e.target.value)} /></Field>
      </div>
      <div className="row">
        <Check label="Show only the newest match of each job" checked={f.latestOnly} onChange={(v) => change("latestOnly", v)} />
        <Check label="Only jobs that fit my preferences (titles and location)" checked={f.preferredOnly} onChange={(v) => change("preferredOnly", v)} />
        <button disabled={!filtered} onClick={() => { setF(NO_FILTERS); setPage(1); }}>Reset filters</button>
      </div>
      {rangeProblem(f.minScore, f.maxScore) && <Alert kind="warning">{rangeProblem(f.minScore, f.maxScore)}</Alert>}
      {res.loading && !res.result && <Loading />}
      <ErrorBox result={res.result} />
      {res.result?.ok && rows.length > 0 && (
        <div className="paginator">
          <button onClick={() => setPage(page - 1)} disabled={page <= 1}>◀ Previous</button><span>Page <b>{page}</b>{total != null && <> of <b>{Math.max(1, Math.ceil(total / HISTORY_PAGE_SIZE))}</b></>} · showing {(page - 1) * HISTORY_PAGE_SIZE + 1}–{(page - 1) * HISTORY_PAGE_SIZE + rows.length}{total != null && <> of <b>{total}</b></>} job(s)</span><button onClick={() => setPage(page + 1)} disabled={!hasNext}>Next ▶</button>
        </div>
      )}
      {res.result?.ok && rows.length === 0 && <Empty message="No saved matches for these filters." hint={filtered ? "Try Reset filters, or score jobs first (see the sections above)." : "Score some jobs first, using the sections above."} />}
      {rows.length > 0 && (
        <>
          <div className="results-table"><DataTable rows={rows} rowKey={(r) => r.match_id} columns={[
            { header: "Applied", render: (r) => <AppliedSwitch jobId={r.job_id} title={r.job_title} company={r.company_name} state={r.application_state}
                onRecorded={() => { res.reload(); countsF.reload(); }} onError={setErr} /> },
            { header: "Job", render: (r) => r.job_title || "(untitled)" }, { header: "Company", render: (r) => r.company_name || "" },
            { header: "Score", render: (r) => <span className="score-cell" title={`Classification: ${r.classification}${r.score_before_role_fit != null && r.role_fit && r.role_fit !== "same" && r.role_fit !== "unknown" ? ` · before role fit: ${scoreLabel(r.score_before_role_fit)}` : ""}`}><b>{scoreLabel(r.match_score)}</b><span className="score-bar"><i style={{ width: `${Math.max(0, Math.min(100, r.match_score))}%` }} /></span></span> },
            { header: "Eligibility", render: (r) => eligLabel(r.eligibility_status) },
            { header: "Experience required", render: (r) => expLabel(r.min_experience_required) },
            { header: "Role fit", render: (r) => roleFitLabel(r.role_fit) },
            { header: "Source", render: (r) => r.source || "—" },
            { header: "Link", render: (r) => httpUrl(r.job_url) ? <a href={httpUrl(r.job_url)!} target="_blank" rel="noopener noreferrer">Open job ↗</a> : "—" },
            { header: "Class", render: (r) => r.classification }, { header: "Recommendation", render: (r) => r.recommendation_code }, { header: "Coverage %", render: (r) => r.data_coverage },
            { header: "Scored", render: (r) => String(r.created_at).slice(0, 16).replace("T", " ") },
            { header: "Details", render: (r) => <button onClick={() => void openId(r.match_id)} aria-label={`Details of ${r.job_title || "job"}`}>Details</button> },
          ]} /></div>
          <Field label="Open a saved match">
            <select value={pick} onChange={(e) => setPick(e.target.value)}>
              {rows.map((r) => <option key={r.match_id} value={r.match_id}>{r.job_title || "(untitled)"} - {g(r.match_score)} - {String(r.created_at).slice(0, 10)}</option>)}
            </select>
          </Field>
          <button onClick={() => void openId(pick)}>Open selected match</button>
          {err && <Alert kind="error">{err}</Alert>}
        </>
      )}
    </>
  );
}
