import { useState } from "react";
import { api } from "../api";
import { PageHeader } from "../components/Layout";
import { Alert, Badge, Check, DataTable, ErrorBox, Field, Loading, MetricRow, Tabs } from "../components/ui";
import { downloadBlob } from "../format";
import { useFetch } from "../hooks";
import { applyLetterEdits, applyResumeEdits, applyScreeningEdits, approvalState, bulletsText, candidateInputs, issueIcon, skillsText, statusBadge } from "../lib/documents";

/**
 * Document Generation: job-specific resume, cover letter and screening answers from your VERIFIED profile.
 * Talks to the FastAPI backend only. Nothing is final until you approve it, and a document with an unsupported claim cannot be approved.
 */
type DocType = "resume" | "cover_letter" | "screening_answers";
const TYPES: Record<DocType, { label: string; path: string }> = {
  resume: { label: "Resume", path: "/api/documents/resume/generate" },
  cover_letter: { label: "Cover letter", path: "/api/documents/cover-letter/generate" },
  screening_answers: { label: "Screening answers", path: "/api/documents/screening/generate" },
};
const list = (xs: string[] | undefined) => (xs && xs.length ? xs.join(", ") : "—");

export default function DocumentGeneration() {
  const resumes = useFetch<any[]>("/api/resumes");
  const active = resumes.data?.find((r) => r.is_active);
  const profile = useFetch<any>(active ? `/api/resumes/${active.resume_id}/profile` : null);
  const aiStatus = useFetch<any>("/api/documents/ai-status");
  const jobs = useFetch<any[]>(active && profile.data ? "/api/jobs" : null, { limit: 200 });
  const [allowUnreviewed, setAllowUnreviewed] = useState(false);
  const [jobId, setJobId] = useState("");
  const [tabName, setTabName] = useState(TYPES.resume.label);

  return (
    <>
      <PageHeader icon="📝" title="Document Generation" />
      <h2>1. Candidate profile</h2>
      {resumes.loading && !resumes.result && <Loading />}
      <ErrorBox result={resumes.result} />
      {resumes.data && !active && <Alert kind="warning">No active resume. Upload one in <b>Resume Manager</b> first.</Alert>}
      {active && profile.loading && !profile.result && <Loading />}
      {active && profile.result && !profile.result.ok && <Alert kind="error">No usable profile for the active resume: {profile.result.error}</Alert>}
      {active && profile.data && (
        <>
          <MetricRow items={[["Active resume", String(active.original_filename).slice(0, 30)], ["Profile review", String(profile.data.review_status).replaceAll("_", " ")], ["Profile version", profile.data.profile_version]]} />
          {profile.data.review_status !== "reviewed" && (
            <>
              <Alert kind="warning">This profile is <b>not reviewed</b>. Documents are written only from verified information - review it in Resume Manager, or confirm below that you want to use it as it is.</Alert>
              <Check label="Use the unreviewed profile anyway" checked={allowUnreviewed} onChange={setAllowUnreviewed} />
            </>
          )}
          <p className="muted">{aiStatus.data?.enabled ? "✍️ Claude wording available (facts only; your name and contact details are never sent)"
            : `⚙️ Written without AI (${aiStatus.data ? aiStatus.data.reason : "status unknown"}). Everything below works without Claude.`}</p>

          <h2>2. Job vacancy</h2>
          {jobs.loading && !jobs.result && <Loading />}
          <ErrorBox result={jobs.result} />
          {jobs.data && jobs.data.length === 0 && <Alert>No jobs yet. Add one in <b>Job Discovery</b> (manual import) or <b>Job Matching</b> (paste a description).</Alert>}
          {jobs.data && jobs.data.length > 0 && (
            <JobSection jobs={jobs.data} jobId={jobId || jobs.data[0].job_id} setJobId={setJobId} usable={profile.data.review_status === "reviewed" || allowUnreviewed} allowUnreviewed={allowUnreviewed}>
              {(id) => (
                <>
                  <h2>3. Documents</h2>
                  <Tabs tabs={Object.values(TYPES).map((t) => t.label)} value={tabName} onChange={setTabName} />
                  {(Object.keys(TYPES) as DocType[]).filter((t) => TYPES[t].label === tabName).map((t) => (
                    <DocPanel key={`${t}-${id}`} type={t} jobId={id} resumeId={active.resume_id} usable={profile.data.review_status === "reviewed" || allowUnreviewed} allowUnreviewed={allowUnreviewed} />
                  ))}
                </>
              )}
            </JobSection>
          )}
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------- job + match
function JobSection({ jobs, jobId, setJobId, usable, allowUnreviewed, children }: { jobs: any[]; jobId: string; setJobId: (id: string) => void; usable: boolean; allowUnreviewed: boolean; children: (id: string) => React.ReactNode }) {
  const job = useFetch<any>(`/api/jobs/${jobId}`);
  const [match, setMatch] = useState<{ jobId: string; report: any } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const check = async () => {
    setBusy(true); setErr(null);
    const r = await api.post("/api/matching/score", { json: { job_id: jobId, allow_unreviewed: allowUnreviewed } });
    setBusy(false);
    if (r.ok) setMatch({ jobId, report: r.data.report }); else setErr(r.error ?? "Request failed");
  };
  const rep = match && match.jobId === jobId ? match.report : null;
  return (
    <>
      <Field label="Select a job">
        <select value={jobId} onChange={(e) => setJobId(e.target.value)}>
          {jobs.map((j) => <option key={j.job_id} value={j.job_id}>{`${j.job_title || "(untitled)"} - ${j.company_name || "(unknown company)"}`}</option>)}
        </select>
      </Field>
      {job.data && (
        <>
          <p className="muted">Required: {list(job.data.mandatory_skills)}  ·  Preferred: {list(job.data.preferred_skills)}</p>
          <details><summary>Job description</summary><pre style={{ whiteSpace: "pre-wrap" }}>{job.data.job_description || "No description."}</pre></details>
        </>
      )}
      <button onClick={check} disabled={!usable || busy}>📊 Check match score</button>
      {busy && <Loading text="Scoring…" />}
      {err && <Alert kind="error">{err}</Alert>}
      {rep && (
        <>
          <MetricRow items={[["Match score", `${Number(rep.match_score)} / 100`], ["Classification", rep.classification], ["Eligibility", rep.eligibility.status]]} />
          <p className="muted">Matched: {list(rep.matched_skills)} · Missing mandatory: {list(rep.missing_mandatory_skills)}</p>
          <p className="muted">Documents only ever mention skills you actually have - missing skills are never added.</p>
        </>
      )}
      {children(jobId)}
    </>
  );
}

// ---------------------------------------------------------------- one document type
function DocPanel({ type, jobId, resumeId, usable, allowUnreviewed }: { type: DocType; jobId: string; resumeId: string; usable: boolean; allowUnreviewed: boolean }) {
  const { label, path } = TYPES[type];
  const docs = useFetch<any[]>("/api/documents", { job_id: jobId, document_type: type, resume_id: resumeId });
  const latestId = docs.data && docs.data.length ? docs.data[0].document_id : null;
  const doc = useFetch<any>(latestId ? `/api/documents/${latestId}` : null);
  const [opts, setOpts] = useState({ max_projects: 3, bullets_recent: 5, bullets_older: 3, length: "medium" });
  const [inputs, setInputs] = useState({ notice_period: "", salary_expectation: "", relocation: "", work_authorization: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const refresh = () => { docs.reload(); doc.reload(); };

  const generate = async () => {
    const body: Record<string, unknown> = { job_id: jobId, allow_unreviewed: allowUnreviewed };
    if (type === "resume") Object.assign(body, { max_projects: opts.max_projects, bullets_recent: opts.bullets_recent, bullets_older: opts.bullets_older });
    if (type === "screening_answers") Object.assign(body, { length: opts.length, candidate_inputs: candidateInputs(inputs) });
    setBusy(true); setErr(null);
    const r = await api.post(path, { json: body });
    setBusy(false);
    if (r.ok) refresh(); else setErr(r.error ?? "Request failed");
  };

  const d = doc.data;
  const exists = Boolean(latestId);
  return (
    <>
      {type === "resume" && (
        <div className="grid cols-3">
          <Field label={`Projects to include: ${opts.max_projects}`}><input type="range" min={0} max={6} value={opts.max_projects} onChange={(e) => setOpts({ ...opts, max_projects: Number(e.target.value) })} /></Field>
          <Field label={`Bullets per recent role: ${opts.bullets_recent}`}><input type="range" min={1} max={8} value={opts.bullets_recent} onChange={(e) => setOpts({ ...opts, bullets_recent: Number(e.target.value) })} /></Field>
          <Field label={`Bullets per older role: ${opts.bullets_older}`}><input type="range" min={0} max={6} value={opts.bullets_older} onChange={(e) => setOpts({ ...opts, bullets_older: Number(e.target.value) })} /></Field>
        </div>
      )}
      {type === "screening_answers" && (
        <>
          <Field label="Answer length"><select value={opts.length} onChange={(e) => setOpts({ ...opts, length: e.target.value })}><option value="short">short</option><option value="medium">medium</option><option value="long">long</option></select></Field>
          <p className="muted">Details that are not in your profile or preferences are never guessed. Provide them here (optional):</p>
          <div className="grid cols-2">
            <Field label="Notice period"><input value={inputs.notice_period} onChange={(e) => setInputs({ ...inputs, notice_period: e.target.value })} /></Field>
            <Field label="Salary expectation"><input value={inputs.salary_expectation} onChange={(e) => setInputs({ ...inputs, salary_expectation: e.target.value })} /></Field>
            <Field label="Willing to relocate? (yes/no)"><input value={inputs.relocation} onChange={(e) => setInputs({ ...inputs, relocation: e.target.value })} /></Field>
            <Field label="Work authorisation"><input value={inputs.work_authorization} onChange={(e) => setInputs({ ...inputs, work_authorization: e.target.value })} /></Field>
          </div>
        </>
      )}
      <button className="primary" onClick={generate} disabled={!usable || busy}>{`${exists ? "🔄 Regenerate" : "✨ Generate"} ${label.toLowerCase()}`}</button>
      {busy && <Loading text="Writing from your verified profile…" />}
      {err && <Alert kind="error">{err}</Alert>}
      {docs.loading && !docs.result && <Loading />}
      <ErrorBox result={docs.result} />
      {docs.data && !exists && <Alert>No {label.toLowerCase()} for this job yet.</Alert>}
      {exists && doc.loading && !d && <Loading />}
      <ErrorBox result={doc.result} />
      {d && <DocBody key={d.document_id} type={type} label={label} doc={d} refresh={refresh} />}
    </>
  );
}

function DocBody({ type, label, doc, refresh }: { type: DocType; label: string; doc: any; refresh: () => void }) {
  const [nonce, setNonce] = useState(0);
  const [ack, setAck] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [exportMsg, setExportMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const v = doc.validation;
  const issues = (sev: string) => (v.issues as any[]).filter((i) => i.severity === sev);

  const revalidate = async () => { await api.post(`/api/documents/${doc.document_id}/validate`); refresh(); };
  const approve = async () => {
    setErr(null);
    const r = await api.post(`/api/documents/${doc.document_id}/approve`, { json: { acknowledge_warnings: ack } });
    if (r.ok) refresh(); else setErr(r.error ?? "Request failed");
  };
  const exportDocs = async () => {
    const r = await api.post(`/api/documents/${doc.document_id}/export`, { json: { formats: ["docx", "pdf"] } });
    setExportMsg({ ok: r.ok, text: r.ok ? "Exported." : r.error ?? "Request failed" });
    if (r.ok) refresh();
  };
  const download = async (fmt: string) => {
    const r = await api.get<Blob>(`/api/documents/${doc.document_id}/download/${fmt}`);
    if (!r.ok) { setExportMsg({ ok: false, text: r.error ?? "Download failed" }); return; }
    const name = [...doc.exports].reverse().find((e: any) => e.format === fmt)?.download_name ?? `document.${fmt}`;
    downloadBlob(name, r.data as Blob, fmt === "pdf" ? "application/pdf" : "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
  };
  const st = approvalState(doc, ack);

  return (
    <>
      <h3>{label} - version {doc.version}</h3>
      {doc.profile_stale && <Alert kind="warning">Your profile changed after this document was generated. Validate it again or regenerate.</Alert>}
      {(doc.notes as string[]).map((n) => <p key={n} className="muted">ℹ️ {n}</p>)}
      {doc.generator === "claude+deterministic" && <p className="muted">✍️ Worded by Claude ({doc.ai_model}) and fact-checked.</p>}

      <p><b>Fact check:</b> {statusBadge(v.status)} · {v.claims_checked} claim(s) checked</p>
      {([[issues("error"), "Must fix before approval", true], [issues("warning"), "Please review", true], [issues("info"), "Notes", false]] as Array<[any[], string, boolean]>).map(([group, title, open]) => group.length > 0 && (
        <details key={title} open={open}><summary>{title} ({group.length})</summary>
          {group.map((i, n) => <p key={n}>{issueIcon(i.severity)} <code>{i.field}</code> - {i.message}</p>)}</details>
      ))}
      <button onClick={revalidate}>🔎 Run fact check again</button>

      <Editor key={`${doc.document_id}_${nonce}`} type={type} doc={doc} onCancel={() => setNonce(nonce + 1)} onSaved={() => { setNonce(nonce + 1); refresh(); }} />

      <p><b>Status:</b> {statusBadge(doc.review_status)}</p>
      {st.approved ? <Alert kind="success">Approved: this version is ready to use for the application.</Alert> : (
        <>
          {st.needsAck && <Check label="I have read the warnings and the document is accurate" checked={ack} onChange={setAck} />}
          <button className="primary" onClick={approve} disabled={!st.canApprove}>✅ Approve this version</button>
          {st.blocked && <p className="muted">Fix the errors above (edit the content below) before this can be approved.</p>}
          {err && <Alert kind="error">{err}</Alert>}
        </>
      )}

      <div className="row" style={{ marginTop: 12 }}>
        <button onClick={exportDocs}>⬇️ Export DOCX and PDF</button>
        {["docx", "pdf"].filter((fmt) => (doc.exports as any[]).some((e) => e.format === fmt)).map((fmt) => {
          const name = [...doc.exports].reverse().find((e: any) => e.format === fmt)?.download_name;
          return <button key={fmt} onClick={() => download(fmt)}>{`Download ${fmt.toUpperCase()} (${name})`}</button>;
        })}
      </div>
      {exportMsg && <Alert kind={exportMsg.ok ? "success" : "error"}>{exportMsg.text}</Alert>}
      {!st.approved && <p className="muted">Exports of unapproved documents carry a visible DRAFT marker.</p>}
      {(doc.exports as any[]).flatMap((e) => (e.warnings as string[]).map((w, i) => <Alert key={`${e.format}-${i}-${w}`} kind="warning">{w}</Alert>))}
      <History doc={doc} />
    </>
  );
}

// ---------------------------------------------------------------- preview + edit (explicit Save / Cancel; saving creates a new version)
function Editor({ type, doc, onCancel, onSaved }: { type: DocType; doc: any; onCancel: () => void; onSaved: () => void }) {
  const c = doc.content;
  const [summary, setSummary] = useState<string>(c.summary ?? "");
  const [skills, setSkills] = useState<Record<number, string>>(() => Object.fromEntries((c.skills ?? []).map((g: any, i: number) => [i, skillsText(g.items)])));
  const [exp, setExp] = useState<Record<number, string>>(() => Object.fromEntries((c.experience ?? []).map((e: any, i: number) => [i, bulletsText(e.bullets)])));
  const [proj, setProj] = useState<Record<number, string>>(() => Object.fromEntries((c.projects ?? []).map((p: any, i: number) => [i, bulletsText(p.bullets)])));
  const [ach, setAch] = useState<string>(bulletsText(c.achievements));
  const [paras, setParas] = useState<string[]>(() => (c.paragraphs ?? []).map((p: any) => p.text));
  const [sig, setSig] = useState<string>(c.signature ?? "");
  const [answers, setAnswers] = useState<string[]>(() => (c.answers ?? []).map((a: any) => a.answer));
  const [err, setErr] = useState<string | null>(null);

  const build = () => type === "resume" ? applyResumeEdits(c, { summary, skills, experience: exp, projects: proj, achievements: ach })
    : type === "cover_letter" ? applyLetterEdits(c, paras, sig) : applyScreeningEdits(c, answers);
  const save = async () => {
    setErr(null);
    const r = await api.put(`/api/documents/${doc.document_id}`, { json: { content: build() } });
    if (r.ok) onSaved(); else setErr(`Not saved: ${r.error}`);
  };
  const badge = (s: string) => ({ answered: "✅", draft: "📝 review", needs_input: "✋ your input needed" } as Record<string, string>)[s] ?? s;
  return (
    <form onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <p className="muted">Edit the text below. Nothing is saved until you press Save - saving creates a new version and re-runs the fact check.</p>
      {type === "resume" && (
        <>
          <p><b>{c.header?.full_name || ""}</b> — {c.header?.headline || ""}<br />{[c.header?.email, c.header?.phone, c.header?.location].filter(Boolean).join(" | ")}</p>
          <Field label="Summary"><textarea value={summary} onChange={(e) => setSummary(e.target.value)} /></Field>
          {(c.skills as any[]).map((g, i) => <Field key={`s${i}`} label={g.label}><textarea value={skills[i] ?? ""} onChange={(e) => setSkills({ ...skills, [i]: e.target.value })} /></Field>)}
          {(c.experience as any[]).map((e, i) => (
            <Field key={`e${i}`} label={`${e.job_title} - ${e.company_name} (${e.start_date || "?"} to ${e.is_current ? "present" : e.end_date || "?"})`}>
              <textarea style={{ minHeight: 130 }} value={exp[i] ?? ""} onChange={(ev) => setExp({ ...exp, [i]: ev.target.value })} />
            </Field>
          ))}
          {(c.projects as any[]).map((p, i) => <Field key={`p${i}`} label={`Project: ${p.project_name}`}><textarea value={proj[i] ?? ""} onChange={(ev) => setProj({ ...proj, [i]: ev.target.value })} /></Field>)}
          <Field label="Achievements"><textarea value={ach} onChange={(e) => setAch(e.target.value)} /></Field>
          <p className="muted">Employers, titles, dates, education and certifications come from your profile and cannot be changed here.</p>
        </>
      )}
      {type === "cover_letter" && (
        <>
          <p>{c.salutation}</p>
          {paras.map((p, i) => <Field key={i} label={`Paragraph ${i + 1}`}><textarea style={{ minHeight: 110 }} value={p} onChange={(e) => setParas(paras.map((x, n) => (n === i ? e.target.value : x)))} /></Field>)}
          <Field label="Signature"><input value={sig} onChange={(e) => setSig(e.target.value)} /></Field>
        </>
      )}
      {type === "screening_answers" && (c.answers as any[]).map((a, i) => (
        <Field key={i} label={`${a.question}  (${badge(a.status)})`}>
          <textarea title={a.note ?? undefined} value={answers[i] ?? ""} onChange={(e) => setAnswers(answers.map((x, n) => (n === i ? e.target.value : x)))} />
          {a.note && <span className="muted">{a.note}</span>}
        </Field>
      ))}
      <div className="row"><button className="primary" type="submit">💾 Save changes</button><button type="button" onClick={onCancel}>Cancel</button></div>
      {err && <Alert kind="error">{err}</Alert>}
    </form>
  );
}

// ---------------------------------------------------------------- version history + compare
function History({ doc }: { doc: any }) {
  const versions = useFetch<any[]>(`/api/documents/${doc.document_id}/versions`);
  const vs = versions.data ?? [];
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const ids = vs.map((x) => x.document_id);
  const left = a || ids[0], right = b || ids[ids.length - 1];
  const diff = useFetch<any>(vs.length > 1 && left && right && left !== right ? `/api/documents/${left}/compare/${right}` : null);
  if (vs.length < 1) return null;
  const vLabel = (id: string) => `v${vs.find((x) => x.document_id === id)?.version}`;
  return (
    <details><summary>Version history ({vs.length})</summary>
      <DataTable rows={vs} columns={[{ header: "Version", render: (x) => x.version }, { header: "Created", render: (x) => String(x.generated_at).slice(0, 16).replace("T", " ") }, { header: "By", render: (x) => x.generator },
        { header: "Fact check", render: (x) => x.validation_status }, { header: "Status", render: (x) => <Badge>{x.review_status}</Badge> }, { header: "Exports", render: (x) => x.exports.length }]} />
      {vs.length > 1 && (
        <div className="grid cols-2">
          <Field label="Compare"><select value={left} onChange={(e) => setA(e.target.value)}>{ids.map((id) => <option key={id} value={id}>{vLabel(id)}</option>)}</select></Field>
          <Field label="with"><select value={right} onChange={(e) => setB(e.target.value)}>{ids.map((id) => <option key={id} value={id}>{vLabel(id)}</option>)}</select></Field>
        </div>
      )}
      {diff.data && <pre>{(diff.data.diff as string[]).join("\n") || "No differences."}</pre>}
    </details>
  );
}
