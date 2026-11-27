import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Result } from "../api";
import { AutofillHelper } from "../components/AutofillHelper";
import { PageHeader } from "../components/Layout";
import { Alert, Check, ConfirmButton, DataTable, Empty, ErrorBox, Field, Loading, Metric, Tabs } from "../components/ui";
import { downloadBlob, fmtDt } from "../format";
import { useFetch, useWebConfig } from "../hooks";
import { answerWidget, canShowSubmit, checkRows, fieldsNeedingInput, mappingRows, nextStep, stateLabel } from "../lib/applications";
import { canControl } from "../lib/automation";
import {
  activeTasks, answersPayload, applicationsPagePath, searchJobs, approveDisabled, canPrepareAgain, canReject, canSwitchToHandoff, confirmBody, eligibilityOf, eventWhen, jobLabel, orderJobs, OVERVIEW_TABS,
  safeHttpUrl, showAnswers, showApprove, showRecheck, submitDisabled, TAB_EMPTY, TAB_STATES, tabLabel, warningsOf, type OverviewTab,
} from "../lib/queue";

/** Application Queue: prepare, review, approve and (only where permitted) submit - or apply yourself with a prepared package.
 *  The backend enforces every rule (policy, approval, duplicates); this page only shows them. */
export default function ApplicationQueue() {
  const [sp, setSp] = useSearchParams();
  const [version, setVersion] = useState(0);
  const refresh = () => setVersion((v) => v + 1);
  const jobs = useFetch("/api/jobs", { limit: 200 });
  const matches = useFetch("/api/matching/results", { limit: 200 });
  const apps = useFetch("/api/applications", { limit: 500, v: version });

  const fits = useFetch<any>("/api/jobs/page", { preferred_only: "true", limit: 200 });
  const [showAll, setShowAll] = useState(false);
  const byJob = new Map<string, any>();
  for (const m of Array.isArray(matches.data) ? matches.data : []) if (!byJob.has(m.job_id)) byJob.set(m.job_id, m);            // newest first
  const appByJob = new Map<string, any>((Array.isArray(apps.data) ? apps.data : []).map((a: any) => [a.job_id, a]));
  const allJobs: any[] = Array.isArray(jobs.data) ? jobs.data : [];
  // default list: jobs that fit your preferences + any job that already has an application; "show all" lists every stored job
  const fitIds = fits.result?.ok && Array.isArray(fits.data?.items) ? new Set<string>(fits.data.items.map((j: any) => j.job_id)) : null;
  const pool = showAll || !fitIds ? allJobs : [...(fits.data.items as any[]), ...allJobs.filter((j) => !fitIds.has(j.job_id) && appByJob.has(j.job_id) && !appByJob.get(j.job_id).dismissed_at)];
  const jobList = orderJobs(pool, (id) => byJob.get(id)?.match_score);
  const wanted = sp.get("job");
  const jobId = jobList.some((j) => j.job_id === wanted) ? (wanted as string) : jobList[0]?.job_id;     // the selection lives in the URL, never in a label

  return (
    <>
      <PageHeader icon="📬" title="Application Queue" />
      <p className="muted">Prepared from your verified profile and APPROVED documents only. Nothing is sent without your explicit approval; LinkedIn, Indeed, Naukri and other restricted sites are never automated - you apply yourself with the prepared package.</p>
      <Overview version={version} onChanged={refresh} onPick={(id) => { setSp({ job: id }); setTimeout(() => document.getElementById("job-step")?.scrollIntoView?.({ block: "start" }), 50); }} />

      <h2 id="job-step">1. Job</h2>
      {jobs.loading && !jobs.result ? <Loading /> : !jobs.result?.ok ? <ErrorBox result={jobs.result} />
        : !jobList.length ? <Alert>No jobs yet. Add one in <b>Job Discovery</b> (manual import) or <b>Job Matching</b> (paste a description).</Alert>
          : (
            <>
              {fitIds && <Check label={`Show all stored jobs (${allJobs.length}${allJobs.length >= 200 ? "+" : ""}) instead of only those that fit my preferences`} checked={showAll} onChange={setShowAll} />}
              <JobPicker jobs={jobList} jobId={jobId} labelOf={(j) => jobLabel(j, byJob.get(j.job_id))} onPick={(id) => setSp({ job: id })} />
              <JobDetail key={`d-${jobId}`} jobId={jobId} match={byJob.get(jobId)} existing={appByJob.get(jobId) ?? null} version={version} onChanged={refresh} />
            </>
          )}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ overview
/** Search box with suggestions + the job list. Typing narrows the list to matching jobs (title or company) and selects the first match;
 *  picking a suggestion selects that job. The selection itself lives in the URL, as before. */
function JobPicker({ jobs, jobId, labelOf, onPick }: { jobs: any[]; jobId: string; labelOf: (j: any) => string; onPick: (id: string) => void }) {
  const [q, setQ] = useState("");
  const found = searchJobs(jobs, q, labelOf);
  const shown = q.trim() && found.length ? found : jobs;
  function type(value: string) {
    setQ(value);
    const exact = jobs.find((j) => labelOf(j) === value);                       // a suggestion was picked
    if (exact) { onPick(exact.job_id); return; }
    const hits = searchJobs(jobs, value, labelOf);
    if (value.trim() && hits.length && !hits.some((j) => j.job_id === jobId)) onPick(hits[0].job_id);   // autofill the best match
  }
  return (
    <div className="grid cols-2">
      <Field label="Search jobs (title or company)">
        <input type="search" list="queue-job-suggestions" value={q} placeholder="e.g. AI engineer, Mindtickle" onChange={(e) => type(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && found[0]) { e.preventDefault(); onPick(found[0].job_id); } }} />
        <datalist id="queue-job-suggestions">{found.slice(0, 50).map((j) => <option key={j.job_id} value={labelOf(j)} />)}</datalist>
      </Field>
      <Field label="Select a job">
        <select value={jobId} onChange={(e) => onPick(e.target.value)}>
          {!shown.some((j) => j.job_id === jobId) && <option value={jobId}>{labelOf(jobs.find((j) => j.job_id === jobId) ?? {})}</option>}
          {shown.map((j) => <option key={j.job_id} value={j.job_id}>{labelOf(j)}</option>)}
        </select>
      </Field>
      {q.trim() !== "" && <span className="muted" role="status">{found.length ? `${found.length} matching job(s)` : "No job matches - showing all jobs"}</span>}
    </div>
  );
}

/** How many applications are in these states (one small request; only the total is used). */
function useTotal(states: string[], version: number): number | null {
  const r = useFetch(`${applicationsPagePath(states, 1)}&v=${version}`);
  return r.result?.ok ? Number(r.data?.total ?? 0) : null;
}

function Overview({ version, onChanged, onPick }: { version: number; onChanged: () => void; onPick: (jobId: string) => void }) {
  const [tab, setTab] = useState<OverviewTab>(OVERVIEW_TABS[0]);
  const queue = useFetch("/api/queue", { limit: 100, v: version });
  const counts: Record<OverviewTab, number | null> = {
    "Needs your action": useTotal(TAB_STATES["Needs your action"] as string[], version),
    "Awaiting review / approval": useTotal(TAB_STATES["Awaiting review / approval"] as string[], version),
    "Queued & processing": queue.result?.ok ? activeTasks(queue.data?.tasks).length : null,
    "Manual handoff": useTotal(TAB_STATES["Manual handoff"] as string[], version),
    "Failed / needs verification": useTotal(TAB_STATES["Failed / needs verification"] as string[], version),
  };
  const applied = useTotal(["SUBMITTED", "VERIFIED"], version);
  const loaded = OVERVIEW_TABS.every((t) => counts[t] !== null);
  const allEmpty = loaded && OVERVIEW_TABS.every((t) => counts[t] === 0);
  const appliedLine = applied ? <> Your <b>{applied}</b> submitted application{applied === 1 ? " is" : "s are"} on <Link to="/applied-jobs">Applied Jobs →</Link></> : null;
  return (
    <>
      <h2>Overview</h2>
      {!loaded && <Loading />}
      {allEmpty && (
        <Alert kind="success">
          ✅ Nothing is waiting here. When JobPilot prepares an application for a good match (after the morning job search), it appears here for your review.{appliedLine}
        </Alert>
      )}
      {loaded && !allEmpty && (
        <>
          <Tabs tabs={OVERVIEW_TABS.map((t) => tabLabel(t, counts[t]))} value={tabLabel(tab, counts[tab])}
            onChange={(label) => setTab(OVERVIEW_TABS.find((t) => tabLabel(t, counts[t]) === label) ?? OVERVIEW_TABS[0])} />
          {tab === "Queued & processing" ? <QueueTab version={version} onChanged={onChanged} /> : <StatesTab key={tab} states={TAB_STATES[tab] as string[]} empty={TAB_EMPTY[tab]} version={version} onPick={onPick} />}
          {appliedLine && <p className="muted">{appliedLine}</p>}
        </>
      )}
    </>
  );
}

function StatesTab({ states, empty, version, onPick }: { states: string[]; empty: string; version: number; onPick: (jobId: string) => void }) {
  const page = useFetch(`${applicationsPagePath(states)}&v=${version}`);
  if (page.loading && !page.result) return <Loading />;
  if (!page.result?.ok) return <ErrorBox result={page.result} />;
  const items: any[] = page.data.items;
  if (!items.length) return <Empty message={empty} />;
  return (
    <>
      <DataTable rows={items} rowKey={(r) => r.application_id} columns={[
        { header: "Job", render: (r) => r.job_title || "(untitled)" }, { header: "Company", render: (r) => r.company_name || "" }, { header: "Score", render: (r) => r.match_score ?? "" },
        { header: "Eligibility", render: (r) => r.eligibility || "" }, { header: "State", render: (r) => stateLabel(r.state) }, { header: "Mode", render: (r) => r.mode }, { header: "Updated", render: (r) => fmtDt(r.updated_at) },
        { header: "", render: (r) => <button onClick={() => onPick(r.job_id)} aria-label={`Open ${r.job_title || "application"}`}>Open ▸</button> }]} />
      <p className="muted">{page.data.total} application(s). Press <b>Open</b> to review it below: approve, reject, or get the documents and link to apply yourself.</p>
    </>
  );
}

function QueueTab({ version, onChanged }: { version: number; onChanged: () => void }) {
  const cfg = useWebConfig();
  const q = useFetch("/api/queue", { limit: 100, v: version });
  const [pick, setPick] = useState("");
  const [err, setErr] = useState<Result | null>(null);
  const tasks: any[] = ((q.data?.tasks ?? []) as any[]).filter((t) => ["queued", "processing", "paused"].includes(t.status));
  useEffect(() => { if (tasks.length && !tasks.some((t) => t.task_id === pick)) setPick(tasks[0].task_id); }, [tasks, pick]);
  if (q.loading && !q.result) return <Loading />;
  if (!q.result?.ok) return <ErrorBox result={q.result} />;
  if (!tasks.length) return <Empty message={TAB_EMPTY["Queued & processing"]} />;
  const cur = tasks.find((t) => t.task_id === pick) ?? tasks[0];
  const act = async (a: string) => { const r = await api.post(`/api/queue/${cur.task_id}/${a}`, { admin: true }); setErr(r); if (r.ok) onChanged(); };
  return (
    <>
      <DataTable rows={tasks} rowKey={(t) => t.task_id} columns={[
        { header: "Job", render: (t) => t.job_title || "(untitled)" }, { header: "Company", render: (t) => t.company_name || "" }, { header: "Score", render: (t) => t.match_score ?? "" },
        { header: "Status", render: (t) => t.status }, { header: "Attempts", render: (t) => `${t.attempts ?? 0}/${t.max_attempts ?? 0}` }, { header: "Error", render: (t) => t.last_error || "" }]} />
      <Field label="Task"><select value={cur.task_id} onChange={(e) => setPick(e.target.value)}>{tasks.map((t) => <option key={t.task_id} value={t.task_id}>{`${t.job_title || "(untitled)"} [${t.status}]`}</option>)}</select></Field>
      <div className="row">
        <button disabled={!cfg.adminEnabled || !canControl("pause", cur.status)} onClick={() => act("pause")}>⏸️ Pause</button>
        <button disabled={!cfg.adminEnabled || !canControl("resume", cur.status)} onClick={() => act("resume")}>▶️ Resume</button>
        <button disabled={!cfg.adminEnabled || !canControl("cancel", cur.status)} onClick={() => act("cancel")}>🚫 Cancel</button>
      </div>
      {!cfg.adminEnabled && <p className="muted">Queue controls need ADMIN_TOKEN (see the Settings page).</p>}
      <ErrorBox result={err} />
    </>
  );
}

// ------------------------------------------------------------------------------------------------ job, documents, prepare
function JobDetail({ jobId, match, existing, version, onChanged }: { jobId: string; match: any; existing: any | null; version: number; onChanged: () => void }) {
  const pol = useFetch(`/api/applications/policy/${jobId}`);
  const elig = eligibilityOf(match);
  const applyUrl = safeHttpUrl(pol.data?.apply_url);
  return (
    <>
      <div className="grid cols-3">
        <Metric label="Match score" value={match && match.match_score !== null && match.match_score !== undefined ? `${Number(match.match_score)} / 100` : "not scored"} />
        <Metric label="Classification" value={match?.classification || "—"} />
        <Metric label="Eligibility" value={elig} />
      </div>
      {!match && <p className="muted">Score this job in <b>Job Matching</b> to see eligibility here; it is also checked when you prepare the application.</p>}
      {pol.result?.ok && (pol.data.automation_allowed
        ? <Alert kind="success">🤖 Automation is permitted for this site ({pol.data.provider}). You still approve before anything is submitted.</Alert>
        : <Alert kind="warning">🧑‍💻 Manual handoff: {pol.data.reason}</Alert>)}
      {applyUrl && <p><a href={applyUrl} target="_blank" rel="noopener noreferrer">Open the application page (your own browser)</a></p>}
      <AutofillHelper jobId={jobId} applyUrl={applyUrl} />

      <h2>2. Approved documents</h2>
      <Documents jobId={jobId} version={version} />

      <h2>3. Prepare</h2>
      <Prepare key={`p-${jobId}`} jobId={jobId} elig={elig} existing={existing} onChanged={onChanged} />

      {existing === null ? <Alert>No application yet for this job.</Alert> : <Review key={existing.application_id} aid={existing.application_id} version={version} onChanged={onChanged} />}
    </>
  );
}

function Documents({ jobId, version }: { jobId: string; version: number }) {
  const resume = useFetch("/api/documents", { job_id: jobId, document_type: "resume", v: version });
  const cover = useFetch("/api/documents", { job_id: jobId, document_type: "cover_letter", v: version });
  const screening = useFetch("/api/documents", { job_id: jobId, document_type: "screening_answers", v: version });
  const show = (f: typeof resume) => { const d = Array.isArray(f.data) ? f.data[0] : null; return d ? `v${d.version} - ${d.review_status}` : "none yet"; };
  const rd = Array.isArray(resume.data) ? resume.data[0] : null;
  const approvedResume = rd?.review_status === "approved";
  return (
    <>
      <div className="grid cols-3"><Metric label="Resume" value={show(resume)} /><Metric label="Cover letter" value={show(cover)} /><Metric label="Screening answers" value={show(screening)} /></div>
      {!approvedResume && <Alert kind="warning">There is no approved resume for this job yet. Generate and approve one in <b>Document Generation</b> - unapproved documents are never uploaded.</Alert>}
    </>
  );
}

function Prepare({ jobId, elig, existing, onChanged }: { jobId: string; elig: string; existing: any | null; onChanged: () => void }) {
  const [allowUnreviewed, setAllowUnreviewed] = useState(false);
  const [ackInelig, setAckInelig] = useState(false);
  const [ackDup, setAckDup] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<Result | null>(null);
  async function prepare() {
    setBusy(true); setRes(null);
    const r = await api.post("/api/applications/prepare", { timeoutMs: 180_000, json: { job_id: jobId, allow_unreviewed: allowUnreviewed, acknowledge_ineligible: ackInelig, acknowledge_possible_duplicate: ackDup } });
    setBusy(false); setRes(r);
    if (r.ok) onChanged();
  }
  return (
    <>
      <div className="grid cols-3">
        <Check label="Use my profile even though it is not reviewed" checked={allowUnreviewed} onChange={setAllowUnreviewed} />
        <Check label="I know this job failed the eligibility check - prepare anyway" checked={ackInelig} onChange={setAckInelig} disabled={elig !== "FAIL"} />
        <Check label="This may duplicate another application - continue" checked={ackDup} onChange={setAckDup} />
      </div>
      <button className="primary" disabled={busy || !canPrepareAgain(existing)} onClick={prepare}>{existing ? "🔄 Prepare again" : "🧰 Prepare application"}</button>
      {busy && <Loading text="Preparing: checking permissions, detecting the form and filling it from your verified data…" />}
      <ErrorBox result={res} />
    </>
  );
}

// ------------------------------------------------------------------------------------------------ review (step 4) and approve/submit (step 5)
function Review({ aid, version, onChanged }: { aid: string; version: number; onChanged: () => void }) {
  const det = useFetch(`/api/applications/${aid}`, { v: version });
  const a = det.data;
  if (det.loading && !det.result) return <Loading />;
  if (!det.result?.ok) return <ErrorBox result={det.result} />;
  return <ReviewBody a={a} version={version} onChanged={onChanged} />;
}

function ReviewBody({ a, version, onChanged }: { a: any; version: number; onChanged: () => void }) {
  const aid: string = a.application_id;
  const [err, setErr] = useState<Result | null>(null);
  const run = async (p: Promise<Result>) => { const r = await p; setErr(r.ok ? null : r); if (r.ok) onChanged(); return r; };
  const v = a.validation ?? {}, fs = a.form_summary ?? {};
  const todo = fieldsNeedingInput(a.mapping);
  const uploads: any[] = a.uploads ?? [];

  return (
    <>
      <h2>4. Review</h2>
      <h3>{stateLabel(a.state)}</h3>
      <Alert>{nextStep(a)}</Alert>
      {(a.notes ?? []).map((n: string) => <p key={n} className="muted">ℹ️ {n}</p>)}
      {a.mode === "automated" && (
        <>
          <div className="grid cols-4">
            <Metric label="Form fields" value={fs.fields ?? 0} /><Metric label="Filled" value={fs.filled ?? 0} />
            <Metric label="Need your input" value={fs.needs_input ?? 0} /><Metric label="Files attached" value={uploads.filter((u) => u.verified).length} />
          </div>
          {v.missing_fields?.length > 0 && <Alert kind="error">Still missing: {v.missing_fields.join(", ")}</Alert>}
          <details open={a.state === "AWAITING_USER_INPUT" || a.state === "READY_FOR_REVIEW"}><summary>Form completeness checks</summary>
            <DataTable rows={checkRows(v)} columns={[{ header: "", render: (r) => r.icon }, { header: "Check", render: (r) => r.check }, { header: "Critical", render: (r) => r.critical }, { header: "Detail", render: (r) => r.detail }]} />
          </details>
          <details open><summary>What will be filled in (preview)</summary>
            <DataTable rows={mappingRows(a.mapping)} columns={[{ header: "Field", render: (r) => r.field }, { header: "Required", render: (r) => r.required }, { header: "Value", render: (r) => r.value },
              { header: "Status", render: (r) => r.status }, { header: "Source", render: (r) => r.source }, { header: "Match", render: (r) => r.match }, { header: "Note", render: (r) => r.note }]} />
          </details>
          {uploads.length > 0 && <p><b>Files:</b> {uploads.map((u) => `${String(u.role).replaceAll("_", " ")} → ${u.file_name} (v${u.document_version}, ${u.verified ? "✅ attached" : "🛑 not attached"})`).join(", ")}</p>}
          {a.screenshots?.length > 0 && <Screenshot aid={aid} name={a.screenshots[a.screenshots.length - 1]} />}

          {showAnswers(a, todo) && <AnswersForm key={aid} aid={aid} todo={todo} onSaved={onChanged} />}
          {showRecheck(a) && <button onClick={() => run(api.post(`/api/applications/${aid}/validate`))}>🔎 Re-check the form now</button>}

          <h2>5. Approve and submit</h2>
          {showApprove(a) && <Approve key={`ap-${aid}-${a.state}`} aid={aid} warns={warningsOf(a)} run={run} />}
          {canShowSubmit(a) && <Submit key={`su-${aid}-${a.state}`} aid={aid} run={run} />}
          {canSwitchToHandoff(a) && <button onClick={() => run(api.post(`/api/applications/${aid}/manual-handoff`))}>🧑‍💻 Switch to manual handoff</button>}
        </>
      )}

      {canReject(a) && (a.dismissed_at
        ? <Alert kind="warning">You rejected this application{a.dismissed_reason ? `: ${a.dismissed_reason}` : ""}. Press &apos;Prepare again&apos; above if you change your mind.</Alert>
        : <div style={{ marginTop: 12 }}><ConfirmButton label="🚫 Reject this application" question="Reject this application? It will not be approved or submitted, and its queue entry is cancelled. (You can prepare it again later.)"
          onConfirm={() => run(api.post(`/api/applications/${aid}/reject`, { json: { reason: null } }))} /></div>)}
      <ErrorBox result={err} />

      <SubmissionResult aid={aid} version={version} />
      {["VERIFICATION_PENDING", "SUBMITTED", "MANUAL_HANDOFF_REQUIRED"].includes(a.state) && <RecordOutcome key={`rec-${aid}-${a.state}`} a={a} onChanged={onChanged} />}
      <History aid={aid} version={version} />
    </>
  );
}

function Approve({ aid, warns, run }: { aid: string; warns: any[]; run: (p: Promise<Result>) => Promise<Result> }) {
  const [ack, setAck] = useState(false);
  return (
    <>
      {warns.length > 0 && <Check label="I have reviewed the values above and the warnings; this is accurate" checked={ack} onChange={setAck} />}
      <button className="primary" disabled={approveDisabled(warns.length, ack)} onClick={() => run(api.post(`/api/applications/${aid}/approve`, { json: { acknowledge_warnings: ack }, admin: true }))}>✅ Approve exactly this application</button>
    </>
  );
}

function Submit({ aid, run }: { aid: string; run: (p: Promise<Result>) => Promise<Result> }) {
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState(false);
  async function submit() {
    setBusy(true);
    await run(api.post(`/api/applications/${aid}/submit`, { admin: true, timeoutMs: 180_000 }));
    setBusy(false);
  }
  return (
    <>
      <Alert kind="warning">Pressing Submit sends a REAL application to the employer&apos;s site.</Alert>
      <Check label="I understand and want to submit this application now" checked={sure} onChange={setSure} />
      <button className="primary" disabled={submitDisabled(sure, busy)} onClick={submit}>📨 Submit application</button>
      {busy && <Loading text="Submitting and looking for a confirmation… (do not press anything again)" />}
    </>
  );
}

/** Only the candidate answers consent / legal / unknown questions: nothing is pre-ticked or pre-filled. */
function AnswersForm({ aid, todo, onSaved }: { aid: string; todo: any[]; onSaved: () => void }) {
  const [vals, setVals] = useState<Record<string, string | boolean>>({});
  const [res, setRes] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const r = await api.put(`/api/applications/${aid}/answers`, { json: { answers: answersPayload(vals) } });
    setBusy(false); setRes(r);
    if (r.ok) onSaved();
  }
  return (
    <>
      <h3>Your answers</h3>
      <p className="muted">Sensitive, legal or unknown questions are never answered for you. Type your own answer (kept for this application only).</p>
      <form onSubmit={save} aria-label="Your answers">
        {todo.map((mp) => {
          const label = `${mp.label}${mp.required ? " *" : ""}`, w = answerWidget(mp), id = mp.field_id;
          if (w === "checkbox") return <Check key={id} label={label} checked={vals[id] === true} onChange={(c) => setVals({ ...vals, [id]: c })} />;
          if (w === "select") return <Field key={id} label={label}><select value={String(vals[id] ?? "")} onChange={(e) => setVals({ ...vals, [id]: e.target.value })}><option value="" />{mp.options.map((o: string) => <option key={o}>{o}</option>)}</select></Field>;
          return <Field key={id} label={label}><input value={String(vals[id] ?? "")} title={mp.note ?? undefined} onChange={(e) => setVals({ ...vals, [id]: e.target.value })} /></Field>;
        })}
        <button type="submit" disabled={busy}>💾 Save answers and re-check</button>
      </form>
      <ErrorBox result={res} />
    </>
  );
}

function Screenshot({ aid, name }: { aid: string; name: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let url: string | null = null, live = true;
    api.get(`/api/applications/${aid}/screenshots/${name}`).then((r) => {
      if (live && r.ok && r.data instanceof Blob && typeof URL.createObjectURL === "function") { url = URL.createObjectURL(r.data); setSrc(url); }
    });
    return () => { live = false; if (url && typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(url); };
  }, [aid, name]);
  if (!src) return null;
  return <details><summary>Preview of the filled form (screenshot)</summary><img src={src} alt="Preview of the filled application form" style={{ maxWidth: "100%" }} /></details>;
}

// ------------------------------------------------------------------------------------------------ result, verification, history
function SubmissionResult({ aid, version }: { aid: string; version: number }) {
  const st = useFetch(`/api/applications/${aid}/status`, { v: version });
  const s = st.data;
  if (!s?.outcome) return null;
  return (
    <>
      <h3>Submission result</h3>
      <p><b>{stateLabel(s.state)}</b> · evidence: {s.evidence?.length ? s.evidence.join(", ") : "none"}{s.reference ? <> · reference: <code>{s.reference}</code></> : null}{s.verified_by ? ` · confirmed by: ${s.verified_by}` : ""}</p>
      {s.detail && <p className="muted">{s.detail}</p>}
    </>
  );
}

function RecordOutcome({ a, onChanged }: { a: any; onChanged: () => void }) {
  const aid: string = a.application_id, handoff = a.state === "MANUAL_HANDOFF_REQUIRED", pk = a.handoff ?? {};
  const [what, setWhat] = useState("I submitted it");
  const [ref, setRef] = useState("");
  const [notes, setNotes] = useState("");
  const [res, setRes] = useState<Result | null>(null);
  const url = safeHttpUrl(pk.apply_url);
  async function download(d: any, role: string, fmt: string) {
    const r = await api.get(`/api/documents/${d.document_id}/download/${fmt}`);
    if (r.ok && r.data instanceof Blob) downloadBlob(`${role}_v${d.version}.${fmt}`, r.data, r.data.type);
    else setRes(r);
  }
  async function record(e: React.FormEvent) {
    e.preventDefault();
    const submitted = a.state === "SUBMITTED" ? true : what === "I submitted it";
    const r = await api.post(`/api/applications/${aid}/confirm`, { json: confirmBody(submitted, ref, notes) });
    setRes(r);
    if (r.ok) onChanged();
  }
  return (
    <>
      <h3>{handoff ? "I applied myself" : "Record what you found"}</h3>
      {handoff && (
        <>
          {url && <><p><a href={url} target="_blank" rel="noopener noreferrer">Open the application page</a></p><button onClick={() => api.post(`/api/applications/${aid}/manual-handoff/opened`)}>I opened the page</button></>}
          <p><b>Documents to upload</b> (approved versions):</p>
          {Object.entries(pk.documents ?? {}).filter(([role]) => role !== "screening").map(([role, d]: [string, any]) => (
            <div className="row" key={role}>{["pdf", "docx"].map((fmt) => <button key={fmt} onClick={() => download(d, role, fmt)}>{`⬇️ ${role.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase())} (v${d.version}, ${fmt.toUpperCase()})`}</button>)}</div>
          ))}
          {pk.prepared_answers?.length > 0 && <details><summary>Prepared screening answers (copy into the form)</summary>{pk.prepared_answers.map((x: any) => <div key={x.question}><b>{x.question}</b><pre>{x.answer}</pre></div>)}</details>}
          <details><summary>Your contact details and personal details on file</summary><pre>{JSON.stringify({ ...filled(pk.contact), ...filled(pk.personal_details) }, null, 2)}</pre></details>
          {pk.missing?.length > 0 && <Alert kind="warning">Missing information:<ul>{pk.missing.map((x: string) => <li key={x}>{x}</li>)}</ul></Alert>}
        </>
      )}
      <form onSubmit={record} aria-label="Record outcome">
        {a.state !== "SUBMITTED" && (
          <fieldset><legend>What happened?</legend>
            {["I submitted it", "It was NOT submitted"].map((o) => <label key={o} className="check"><input type="radio" name="conf_what" checked={what === o} onChange={() => setWhat(o)} />{o}</label>)}
          </fieldset>
        )}
        <Field label="Confirmation / reference number (if any)"><input value={ref} onChange={(e) => setRef(e.target.value)} /></Field>
        <Field label="Notes (e.g. confirmation e-mail received)"><textarea value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        <button type="submit">Record</button>
      </form>
      <ErrorBox result={res} />
    </>
  );
}
const filled = (o: Record<string, unknown> | undefined) => Object.fromEntries(Object.entries(o ?? {}).filter(([, v]) => v));

function History({ aid, version }: { aid: string; version: number }) {
  const ev = useFetch(`/api/applications/${aid}/events`, { v: version });
  return (
    <details><summary>History (immutable event log)</summary>
      {Array.isArray(ev.data) && <DataTable rows={ev.data} rowKey={(e: any) => String(e.seq)} columns={[
        { header: "#", render: (e: any) => e.seq }, { header: "When", render: (e: any) => eventWhen(e.at) }, { header: "Event", render: (e: any) => e.type },
        { header: "From", render: (e: any) => e.from_state || "" }, { header: "To", render: (e: any) => e.to_state || "" }, { header: "By", render: (e: any) => e.actor }]} />}
    </details>
  );
}
