import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { PageHeader } from "../components/Layout";
import { Alert, Check, ConfirmButton, DataTable, Empty, ErrorBox, Field, Loading, Metric, Tabs, useFlash } from "../components/ui";
import { fmtDt } from "../format";
import { useFetch } from "../hooks";
import {
  CERT_SPEC, EDUCATION_SPEC, EXPERIENCE_SPEC, PROJECT_SPEC, SEP, SKILL_CATEGORIES, emptyRow, checkResumeFile, formToPayload, profileToForm, titleCase,
  type ProfileForm, type Row, type Spec,
} from "../lib/profileEditor";
import { renderLines } from "../lib/resumeText";

const STATUS_ICON: Record<string, string> = { completed: "✅", parsing: "⏳", pending: "⏳", failed: "❌", needs_ocr: "🖨️" };
const utc = (s: string | null | undefined) => (s ? `${s.slice(0, 16).replace("T", " ")}` : "—");

export default function ResumeManager() {
  const list = useFetch<any[]>("/api/resumes");
  const [selectedId, setSelectedId] = useState<string>("");
  const resumes = list.data;

  return (
    <>
      <PageHeader icon="📄" title="Resume Manager" />
      <h2>1. Upload resume</h2>
      <Uploader onUploaded={(id) => { setSelectedId(id); list.reload(); }} />

      <h2>2. Your resumes</h2>
      {list.loading && !list.result && <Loading />}
      <ErrorBox result={list.result} />
      {resumes && resumes.length === 0 && <Empty message="No resumes uploaded yet." />}
      {resumes && resumes.length > 0 && <Selected resumes={resumes} selectedId={selectedId} setSelectedId={setSelectedId} reload={list.reload} />}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ 1. upload
function Uploader({ onUploaded }: { onUploaded: (id: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "info" | "success" | "warning" | "error"; text: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const pick = (f: File | null) => {
    setMsg(null);
    if (!f) { setFile(null); return; }
    const problem = checkResumeFile(f.name, f.size);
    if (problem) { setFile(null); setMsg({ kind: "error", text: problem }); if (input.current) input.current.value = ""; return; }
    setFile(f);
  };
  const upload = async () => {
    if (!file) return;
    setBusy(true); setMsg(null);
    const form = new FormData();
    form.append("file", file, file.name);
    const r = await api.post("/api/resumes/upload", { form, timeoutMs: 120_000 });
    setBusy(false);
    if (!r.ok) { setMsg({ kind: "error", text: r.error ?? "Upload failed" }); return; }
    const res = r.data.resume;
    onUploaded(res.resume_id);
    if (r.data.duplicate) setMsg({ kind: "info", text: r.data.message });
    else if (res.parsing_status === "completed") setMsg({ kind: "success", text: "Uploaded and parsed. Review the extracted profile below." });
    else setMsg({ kind: "warning", text: `Uploaded, but parsing status is ${res.parsing_status}: ${res.parsing_error ?? ""}` });
    setFile(null);
    if (input.current) input.current.value = "";
  };

  return (
    <>
      <Field label="Master resume (PDF or DOCX)"><input ref={input} type="file" accept=".pdf,.docx" aria-label="Master resume (PDF or DOCX)" onChange={(e) => pick(e.target.files?.[0] ?? null)} /></Field>
      {file && <p className="muted"><b>{file.name}</b> — {(file.size / 1024).toFixed(1)} KB</p>}
      <button className="primary" disabled={!file || busy} onClick={upload}>Upload &amp; parse</button>
      {busy && <Loading text="Uploading and parsing…" />}
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ 2. list + selected resume
function Selected({ resumes, selectedId, setSelectedId, reload }: { resumes: any[]; selectedId: string; setSelectedId: (id: string) => void; reload: () => void }) {
  const ids = resumes.map((r) => r.resume_id);
  const selected = ids.includes(selectedId) ? selectedId : ids[0];
  const resume = resumes.find((r) => r.resume_id === selected)!;
  const [reparseOk, setReparseOk] = useState(false);
  const [deleteOk, setDeleteOk] = useState(false);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const act = async (label: string, fn: () => Promise<{ ok: boolean; error?: string }>) => {
    setBusy(label); setErr(null);
    const r = await fn();
    setBusy("");
    if (r.ok) { setReparseOk(false); setDeleteOk(false); reload(); } else setErr(r.error ?? "Failed");
  };

  return (
    <>
      <DataTable rows={resumes} rowKey={(r) => r.resume_id} columns={[
        { header: "Active", render: (r) => (r.is_active ? "⭐" : "") }, { header: "File", render: (r) => r.original_filename }, { header: "Type", render: (r) => String(r.file_type).toUpperCase() },
        { header: "Uploaded", render: (r) => `${utc(r.uploaded_at)} UTC` }, { header: "Parsing", render: (r) => `${STATUS_ICON[r.parsing_status] ?? ""} ${r.parsing_status}` },
        { header: "Review", render: (r) => r.review_status || "—" }, { header: "Profile v", render: (r) => r.profile_version || "—" },
      ]} />
      <Field label="Select a resume to review">
        <select value={selected} onChange={(e) => setSelectedId(e.target.value)} aria-label="Select a resume to review">
          {resumes.map((r) => <option key={r.resume_id} value={r.resume_id}>{r.original_filename} — {String(r.uploaded_at).slice(0, 10)}{r.is_active ? " (active)" : ""}</option>)}
        </select>
      </Field>
      <div className="grid cols-3">
        <div><button disabled={resume.is_active || busy !== ""} onClick={() => act("activate", () => api.post(`/api/resumes/${selected}/activate`))}>⭐ Make active master resume</button></div>
        <div>
          <Check label="Allow re-parse (current profile is kept as a previous version)" checked={reparseOk} onChange={setReparseOk} />
          <button disabled={!reparseOk || busy !== ""} onClick={() => act("reparse", () => api.post(`/api/resumes/${selected}/parse`, { params: { force: "true" }, timeoutMs: 120_000 }))}>🔄 Re-parse</button>
        </div>
        <div>
          <Check label="I want to permanently delete this resume and its profile" checked={deleteOk} onChange={setDeleteOk} />
          <ConfirmButton label="🗑️ Delete" question="Permanently delete this resume and its profile?" disabled={!deleteOk || busy !== ""} onConfirm={() => act("delete", () => api.del(`/api/resumes/${selected}`))} />
        </div>
      </div>
      {busy === "reparse" && <Loading text="Parsing…" />}
      {err && <Alert kind="error">{err}</Alert>}

      {resume.parsing_status !== "completed"
        ? <Alert kind="error">Parsing status: <b>{resume.parsing_status}</b> — {resume.parsing_error || "no details"}</Alert>
        : <ProfileSection key={selected} resumeId={selected} />}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ 3-7. profile
function ProfileSection({ resumeId }: { resumeId: string }) {
  const prof = useFetch<any>(`/api/resumes/${resumeId}/profile`);
  const [nonce, setNonce] = useState(0);
  const p = prof.data;
  if (prof.loading && !prof.result) return <Loading />;
  if (!p) return <ErrorBox result={prof.result} />;
  return (
    <>
      <ReviewStatus p={p} />
      <h2>4. Extracted profile — review and correct</h2>
      <p className="muted">Nothing is saved until you press <b>Save changes</b>. Items in table cells that hold lists are separated by <code>{SEP.trim()}</code>. Corrections are stored as a new profile version; the previous one is kept.</p>
      <ProfileEditor key={`${resumeId}_${p.profile_version}_${nonce}`} resumeId={resumeId} profile={p} onCancel={() => setNonce((n) => n + 1)} onSaved={() => { setNonce((n) => n + 1); prof.reload(); }} />
      <details><summary>Raw profile JSON</summary><pre>{JSON.stringify(p, null, 2)}</pre></details>
      <Overview p={p} />
      <Versions resumeId={resumeId} />
      <Generated resumeId={resumeId} />
    </>
  );
}

function ReviewStatus({ p }: { p: any }) {
  const flags = p.review_flags ?? [], missing = p.missing_fields ?? [], warnings = p.validation_warnings ?? [];
  const ICON: Record<string, string> = { error: "🛑", warning: "⚠️", info: "ℹ️" };
  return (
    <>
      <h2>3. Review status</h2>
      <div className="grid cols-4">
        <Metric label="Parsing" value={String(p.parsing_status).replaceAll("_", " ")} /><Metric label="Review" value={String(p.review_status).replaceAll("_", " ")} />
        <Metric label="Profile version" value={p.profile_version} /><Metric label="Last updated" value={utc(p.updated_at)} />
      </div>
      <p className="muted">Extraction method: <code>{p.extraction_method}</code> · Confidence score: not assigned (no artificial scores)</p>
      <details open={flags.length > 0}><summary>⚠️ Fields needing review ({flags.length})</summary>
        {flags.length ? flags.map((f: any, i: number) => <div key={i}>• <b>{f.field}</b> — {f.reason}</div>) : <div>Nothing flagged.</div>}
      </details>
      <details><summary>❔ Missing information ({missing.length})</summary>{missing.length ? missing.join(", ") : "Nothing missing."}</details>
      <details open={warnings.some((w: any) => w.severity !== "info")}><summary>🔎 Validation warnings ({warnings.length})</summary>
        {warnings.length ? warnings.map((w: any, i: number) => <div key={i}>{ICON[w.severity] ?? "•"} <code>{w.field}</code> — {w.message}</div>) : <div>No warnings.</div>}
      </details>
    </>
  );
}

// ------------------------------------------------------------------------------------------------ editor (all state local until Save)
const TABS = ["Personal information", "Professional summary", "Technical skills", "Work experience", "Projects & achievements", "Education", "Certifications"];

function ProfileEditor({ resumeId, profile, onCancel, onSaved }: { resumeId: string; profile: any; onCancel: () => void; onSaved: () => void }) {
  const [f, setF] = useState<ProfileForm>(() => profileToForm(profile));
  const [tab, setTab] = useState(TABS[0]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { setFlash } = useFlash();
  const set = <K extends keyof ProfileForm>(k: K, v: ProfileForm[K]) => setF((cur) => ({ ...cur, [k]: v }));
  const contact = (k: keyof ProfileForm["contact"], v: string) => setF((cur) => ({ ...cur, contact: { ...cur.contact, [k]: v } }));

  const save = async () => {
    setSaving(true); setError(null);
    const r = await api.put(`/api/resumes/${resumeId}/profile`, { json: formToPayload(f), timeoutMs: 30_000 });
    setSaving(false);
    if (r.ok) { setFlash(`Saved as profile version ${r.data.profile_version} (${String(r.data.review_status).replaceAll("_", " ")}).`); onSaved(); }
    else setError(`Not saved: ${r.error}`);
  };

  // A div, not a <form>: the shared Tabs buttons have no type="button" and would submit a form. Nothing is saved until Save is pressed.
  return (
    <div role="form" aria-label="Profile editor">
      <Tabs tabs={TABS} value={tab} onChange={setTab} />
      {tab === TABS[0] && (
        <div className="grid cols-2">
          <Field label="Full name"><input value={f.contact.full_name} onChange={(e) => contact("full_name", e.target.value)} /></Field>
          <Field label="Email"><input value={f.contact.email} onChange={(e) => contact("email", e.target.value)} /></Field>
          <Field label="Phone"><input value={f.contact.phone} onChange={(e) => contact("phone", e.target.value)} /></Field>
          <Field label="Location"><input value={f.contact.location} onChange={(e) => contact("location", e.target.value)} /></Field>
          <Field label="LinkedIn URL"><input value={f.contact.linkedin_url} onChange={(e) => contact("linkedin_url", e.target.value)} /></Field>
          <Field label="GitHub URL"><input value={f.contact.github_url} onChange={(e) => contact("github_url", e.target.value)} /></Field>
          <Field label="Portfolio URL"><input value={f.contact.portfolio_url} onChange={(e) => contact("portfolio_url", e.target.value)} /></Field>
        </div>
      )}
      {tab === TABS[1] && (
        <>
          <Field label="Professional summary"><textarea value={f.summary} onChange={(e) => set("summary", e.target.value)} /></Field>
          <Field label="Current job title"><input value={f.title} onChange={(e) => set("title", e.target.value)} /></Field>
          <Field label="Total experience (years)"><input value={f.years} onChange={(e) => set("years", e.target.value)} /></Field>
          <p className="muted">Calculated from employment dates when possible. Leave blank if unknown.</p>
          <Field label="Primary domains (one per line)"><textarea value={f.domains} onChange={(e) => set("domains", e.target.value)} /></Field>
          <Field label="Career objective"><textarea value={f.objective} onChange={(e) => set("objective", e.target.value)} /></Field>
        </>
      )}
      {tab === TABS[2] && (
        <>
          <p className="muted">One skill per line (commas also work). Add missing skills or delete incorrect ones.</p>
          <div className="grid cols-2">
            {SKILL_CATEGORIES.map((cat) => <Field key={cat} label={titleCase(cat)}><textarea value={f.skills[cat] ?? ""} onChange={(e) => setF((cur) => ({ ...cur, skills: { ...cur.skills, [cat]: e.target.value } }))} /></Field>)}
          </div>
        </>
      )}
      {tab === TABS[3] && <TableEditor name="Work experience" spec={EXPERIENCE_SPEC} rows={f.experience} onChange={(r) => set("experience", r)} hint="Dates: YYYY-MM or YYYY; leave the end date blank if current." />}
      {tab === TABS[4] && (
        <>
          <TableEditor name="Projects" spec={PROJECT_SPEC} rows={f.projects} onChange={(r) => set("projects", r)} />
          <Field label="Other achievements & awards (one per line)"><textarea value={f.achievements} onChange={(e) => set("achievements", e.target.value)} /></Field>
        </>
      )}
      {tab === TABS[5] && <TableEditor name="Education" spec={EDUCATION_SPEC} rows={f.education} onChange={(r) => set("education", r)} />}
      {tab === TABS[6] && <TableEditor name="Certifications" spec={CERT_SPEC} rows={f.certifications} onChange={(r) => set("certifications", r)} />}

      <Check label="Mark this profile as reviewed when saving" checked={f.reviewed} onChange={(v) => set("reviewed", v)} />
      <div className="row">
        <button type="button" className="primary" disabled={saving} onClick={() => void save()}>💾 Save changes</button>
        <button type="button" onClick={onCancel} disabled={saving}>Cancel</button>
        {saving && <Loading text="Saving…" />}
      </div>
      {error && <Alert kind="error">{error}</Alert>}
    </div>
  );
}

function TableEditor({ name, spec, rows, onChange, hint }: { name: string; spec: Spec; rows: Row[]; onChange: (rows: Row[]) => void; hint?: string }) {
  const edit = (i: number, field: string, v: string | boolean) => onChange(rows.map((r, j) => (j === i ? { ...r, [field]: v } : r)));
  return (
    <div>
      {hint && <p className="muted">{hint}</p>}
      {rows.length === 0 && <p className="muted">No rows yet.</p>}
      <div className="table-wrap"><table aria-label={name}>
        <thead><tr>{spec.map(([f]) => <th key={f}>{f}</th>)}<th /></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {spec.map(([f, kind]) => (
                <td key={f}>{kind === "bool"
                  ? <input type="checkbox" aria-label={`${f} row ${i + 1}`} checked={Boolean(r[f])} onChange={(e) => edit(i, f, e.target.checked)} />
                  : <input aria-label={`${f} row ${i + 1}`} value={String(r[f] ?? "")} onChange={(e) => edit(i, f, e.target.value)} />}</td>
              ))}
              <td><button type="button" aria-label={`Delete ${name} row ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>✕</button></td>
            </tr>
          ))}
        </tbody>
      </table></div>
      <button type="button" onClick={() => onChange([...rows, emptyRow(spec)])}>➕ Add row</button>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ 5. overview
function Overview({ p }: { p: any }) {
  const [tab, setTab] = useState("Skills");
  const skills = Object.entries(p.technical_skills ?? {}).filter(([, items]) => (items as string[]).length);
  const exp = p.work_experience ?? [], proj = p.projects ?? [], edu = p.education ?? [], certs = p.certifications ?? [];
  return (
    <>
      <h2>5. Profile overview</h2>
      <p><b>Verification status:</b> {p.review_status === "reviewed" ? "✅ reviewed" : "⏳ pending review"} · profile version {p.profile_version} · extraction: {p.extraction_method} · missing fields: {(p.missing_fields ?? []).length}</p>
      <Tabs tabs={["Skills", "Experience", "Projects", "Education & certifications"]} value={tab} onChange={setTab} />
      {tab === "Skills" && (skills.length ? skills.map(([cat, items]) => <p key={cat}><b>{titleCase(cat)}:</b> {(items as string[]).join(", ")}</p>) : <Alert>No skills extracted yet.</Alert>)}
      {tab === "Experience" && (exp.length ? exp.map((w: any, i: number) => (
        <div key={i}><p><b>{w.job_title || "(title unknown)"}</b> — {w.company_name || "(company unknown)"}<br />{w.start_date || "?"} → {w.is_current ? "present" : w.end_date || "?"}</p>
          <ul>{(w.responsibilities ?? []).slice(0, 6).map((r: string, j: number) => <li key={j}>{r}</li>)}</ul></div>
      )) : <Alert>No experience extracted yet.</Alert>)}
      {tab === "Projects" && (proj.length ? proj.map((q: any, i: number) => (
        <div key={i}><p><b>{q.project_name || "(unnamed)"}</b>{q.technologies?.length ? ` — ${q.technologies.join(", ")}` : ""}</p>
          <ul>{(q.responsibilities ?? []).slice(0, 4).map((r: string, j: number) => <li key={j}>{r}</li>)}</ul></div>
      )) : <Alert>No projects extracted yet.</Alert>)}
      {tab === "Education & certifications" && (edu.length || certs.length ? (
        <>
          {edu.map((e: any, i: number) => <p key={`e${i}`}><b>{e.degree || "(degree unknown)"}</b> {e.specialization || ""} — {e.institution || ""} ({e.graduation_year || "?"})</p>)}
          {certs.map((c: any, i: number) => <p key={`c${i}`}>🎓 {c.certification_name} — {c.issuing_organization || ""}</p>)}
        </>
      ) : <Alert>No education or certifications extracted yet.</Alert>)}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ 6-7. versions, generated resumes
function Versions({ resumeId }: { resumeId: string }) {
  const v = useFetch<any[]>(`/api/resumes/${resumeId}/profile/versions`);
  return (
    <>
      <h2>6. Profile version history</h2>
      {v.data?.length ? <DataTable rows={v.data} columns={[
        { header: "Version", render: (r) => r.profile_version }, { header: "Updated", render: (r) => fmtDt(r.updated_at) }, { header: "Method", render: (r) => r.extraction_method }, { header: "Review", render: (r) => r.review_status },
      ]} /> : <Alert>Only the current profile version exists.</Alert>}
    </>
  );
}

function Generated({ resumeId }: { resumeId: string }) {
  const docs = useFetch<any[]>("/api/documents", { document_type: "resume", resume_id: resumeId, limit: 50 });
  const [pickRaw, setPick] = useState("");
  const ids = (docs.data ?? []).map((d) => d.document_id);
  const pick = ids.includes(pickRaw) ? pickRaw : ids[0] ?? null;
  const doc = useFetch<any>(pick ? `/api/documents/${pick}` : null);
  return (
    <>
      <h2>7. Generated resumes</h2>
      {docs.loading && !docs.result && <Loading />}
      <ErrorBox result={docs.result} />
      {docs.data && docs.data.length === 0 && <Alert>No job-specific resumes generated yet. Create one on the <Link to="/document-generation">Document Generation</Link> page.</Alert>}
      {docs.data && docs.data.length > 0 && (
        <>
          <DataTable rows={docs.data} rowKey={(d) => d.document_id} columns={[
            { header: "Job", render: (d) => d.job_title || "" }, { header: "Company", render: (d) => d.company_name || "" }, { header: "Version", render: (d) => d.version },
            { header: "Fact check", render: (d) => d.validation_status }, { header: "Status", render: (d) => d.review_status }, { header: "Generated", render: (d) => fmtDt(d.generated_at) },
          ]} />
          <Field label="Preview a generated resume">
            <select value={pick ?? ""} onChange={(e) => setPick(e.target.value)} aria-label="Preview a generated resume">
              {docs.data.map((d) => <option key={d.document_id} value={d.document_id}>{d.job_title || "?"} - {d.company_name || "?"} (v{d.version}, {d.review_status})</option>)}
            </select>
          </Field>
          {doc.data && (
            <>
              <Field label="Preview (plain text of the generated resume)"><textarea readOnly disabled style={{ height: 360 }} value={renderLines("resume", doc.data.content).join("\n")} /></Field>
              {doc.data.review_status !== "approved" && <p className="muted">Not approved yet - approve it on the Document Generation page before it can be used in an application.</p>}
            </>
          )}
        </>
      )}
    </>
  );
}
