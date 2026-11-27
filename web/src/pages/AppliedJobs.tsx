import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { PageHeader } from "../components/Layout";
import { RejectionInbox } from "../components/RejectionInbox";
import { Alert, DataTable, Empty, ErrorBox, Field, Loading, MetricRow, Paginator, useFlash } from "../components/ui";
import { useFetch } from "../hooks";
import { stateLabel } from "../lib/applications";
import { appliedPath, appliedRows, buildAppliedParams, canRecordOutcome, confirmBody, eventRows, PAGE_SIZE, PRESETS, responseLabel, screenshotUrl } from "../lib/applied";

/** Applied Jobs: only evidence-backed applications are shown as submitted. One application per job is enforced by the backend. */
export default function AppliedJobs() {
  const [preset, setPreset] = useState(Object.keys(PRESETS)[0]);
  const [q, setQ] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const { params, states } = buildAppliedParams(preset, q, from, to);
  const summary = useFetch<any>("/api/dashboard/summary", { days: 7 });
  const probe = useFetch<any>(appliedPath("/api/applications/page", { ...params, limit: 1 }, states));
  const total: number = probe.data?.total ?? 0;
  const skip = (Math.min(page, Math.max(1, Math.ceil(total / PAGE_SIZE))) - 1) * PAGE_SIZE;
  const res = useFetch<any>(total ? appliedPath("/api/applications/page", { ...params, limit: PAGE_SIZE, skip }, states) : null);
  const items: any[] = res.data?.items ?? [];
  const [sel, setSel] = useState("");
  useEffect(() => { if (items.length && !items.some((r) => r.application_id === sel)) setSel(items[0].application_id); }, [items, sel]);
  const reset = (fn: () => void) => { fn(); setPage(1); };
  const a = summary.data?.applications;

  return (
    <>
      <PageHeader icon="🗂️" title="Applied Jobs" />
      <p className="muted">Submitted = the site showed a confirmation (or you recorded it). &apos;Outcome unknown&apos; needs your check - it is never retried automatically. Each job can have only one application record.</p>
      {a && <MetricRow items={[["Submitted", a.submitted], ["Verified", a.verified, "Reference number or your own confirmation"], ["Outcome unknown", a.by_state?.VERIFICATION_PENDING ?? 0, "Please verify"],
        ["Failed", a.failed, "Not submitted"], ["You apply (manual)", a.by_state?.MANUAL_HANDOFF_REQUIRED ?? 0]]} />}
      <RejectionInbox onChanged={() => { res.reload(); probe.reload(); }} />
      <div className="grid" style={{ gridTemplateColumns: "2fr 2fr 1fr 1fr" }}>
        <Field label="Show"><select value={preset} onChange={(e) => reset(() => setPreset(e.target.value))}>{Object.keys(PRESETS).map((p) => <option key={p}>{p}</option>)}</select></Field>
        <Field label="Search company, job, reference or notes"><input value={q} onChange={(e) => reset(() => setQ(e.target.value))} /></Field>
        <Field label="From"><input type="date" value={from} onChange={(e) => reset(() => setFrom(e.target.value))} /></Field>
        <Field label="To"><input type="date" value={to} onChange={(e) => reset(() => setTo(e.target.value))} /></Field>
      </div>
      {probe.loading && !probe.result && <Loading />}
      <ErrorBox result={probe.result} />
      {probe.result?.ok && !total && <Empty message="No applications match." hint="Prepare one on the Application Queue page." />}
      {total > 0 && (
        <>
          <Paginator total={total} size={PAGE_SIZE} page={page} onPage={setPage} />
          <ErrorBox result={res.result} />
          <DataTable rows={appliedRows(items)} rowKey={(r) => r.id} columns={[
            { header: "Company", render: (r) => r.company }, { header: "Job title", render: (r) => r.title }, { header: "Date", render: (r) => r.date }, { header: "Status", render: (r) => r.status },
            { header: "How", render: (r) => r.how }, { header: "Platform", render: (r) => r.platform }, { header: "Confirmation ref", render: (r) => r.reference },
            { header: "Confirmed by", render: (r) => r.confirmedBy }, { header: "Notes", render: (r) => r.notes }, { header: "Employer response", render: (r) => r.response }, { header: "Skipped by you", render: (r) => r.rejected },
          ]} />
          {items.length > 0 && (
            <>
              <Field label="Details"><select value={sel} onChange={(e) => setSel(e.target.value)}>{items.map((r) => <option key={r.application_id} value={r.application_id}>{r.company_name || "?"} - {r.job_title || "?"}</option>)}</select></Field>
              {sel && items.some((r) => r.application_id === sel) && <Detail key={sel} app={items.find((r) => r.application_id === sel)!} onChanged={() => { res.reload(); probe.reload(); summary.reload(); }} />}
            </>
          )}
        </>
      )}
    </>
  );
}

function Detail({ app, onChanged }: { app: any; onChanged: () => void }) {
  const id: string = app.application_id;
  const { setFlash } = useFlash();
  const stt = useFetch<any>(`/api/applications/${id}/status`);
  const detail = useFetch<any>(`/api/applications/${id}`);
  const events = useFetch<any[]>(`/api/applications/${id}/events`);
  const [notes, setNotes] = useState<string>(app.user_notes || "");
  const [notesErr, setNotesErr] = useState<string | null>(null);
  const [what, setWhat] = useState("I submitted it");
  const [ref, setRef] = useState("");
  const [note, setNote] = useState("");
  const [outErr, setOutErr] = useState<string | null>(null);
  const [respErr, setRespErr] = useState<string | null>(null);
  const s = stt.data;
  const shots: string[] = detail.data?.screenshots ?? [];

  async function saveNotes(e: FormEvent) {
    e.preventDefault(); setNotesErr(null);
    const r = await api.put(`/api/applications/${id}/notes`, { json: { notes } });
    if (r.ok) { setFlash("Notes saved."); onChanged(); } else setNotesErr(`Not saved: ${r.error}`);
  }
  async function record(e: FormEvent) {
    e.preventDefault(); setOutErr(null);
    const r = await api.post(`/api/applications/${id}/confirm`, { json: confirmBody(app.state, what, ref, note) });
    if (r.ok) { setFlash("Outcome recorded."); onChanged(); stt.reload(); events.reload(); } else setOutErr(`Not recorded: ${r.error}`);
  }

  async function employer(undo: boolean) {
    setRespErr(null);
    const r = await api.post(`/api/applications/${id}/employer-rejected${undo ? "/undo" : ""}`);
    if (r.ok) { setFlash(undo ? "Rejection removed." : "Marked as rejected by the employer."); onChanged(); events.reload(); } else setRespErr(`Not changed: ${r.error}`);
  }
  const rejected = app.employer_response?.outcome === "rejected";
  const canMark = ["SUBMITTED", "VERIFIED", "VERIFICATION_PENDING", "MANUAL_HANDOFF_REQUIRED"].includes(app.state) && !app.dismissed_at;

  return (
    <>
      <h3>{app.company_name || "?"} — {app.job_title || "?"}</h3>
      {canMark && (
        <p>
          <b>Employer response:</b> {rejected ? responseLabel(app.employer_response) : "no answer recorded yet"}
          {rejected && app.employer_response?.phrase ? <span className="muted"> · “{app.employer_response.phrase}” from {app.employer_response.sender_domain}</span> : null}{" "}
          <button type="button" onClick={() => employer(rejected)}>{rejected ? "↩️ Undo rejected" : "❌ Mark as rejected by employer"}</button>
          {respErr && <Alert kind="error">{respErr}</Alert>}
        </p>
      )}
      {s && (
        <>
          <p><b>{stateLabel(s.state)}</b> · evidence: {(s.evidence ?? []).join(", ") || "none recorded"}{s.reference ? <> · reference <code>{s.reference}</code></> : null}{s.verified_by ? ` · confirmed by ${s.verified_by}` : ""}</p>
          {s.detail && <p className="muted">{s.detail}</p>}
          {s.needs_manual_verification && <Alert kind="warning">We cannot tell whether this went through. Check your e-mail or the employer&apos;s site, then record what you found below.</Alert>}
        </>
      )}
      {shots.length > 0 && (
        <details><summary>Evidence: last page screenshot (stored locally)</summary><img alt="Last page screenshot" style={{ maxWidth: "100%" }} src={screenshotUrl(id, shots[shots.length - 1])} /></details>
      )}
      <form onSubmit={saveNotes} aria-label="Notes">
        <Field label="Your notes (private; never used by automation)"><textarea maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        <button type="submit">💾 Save notes</button>
        {notesErr && <Alert kind="error">{notesErr}</Alert>}
      </form>
      {canRecordOutcome(app.state) && (
        <form onSubmit={record} aria-label="Record outcome">
          <p><b>Record what you found</b></p>
          {app.state !== "SUBMITTED" && (
            <fieldset><legend>What happened?</legend>
              {["I submitted it", "It was NOT submitted"].map((o) => <label key={o} className="check"><input type="radio" name={`what_${id}`} checked={what === o} onChange={() => setWhat(o)} />{o}</label>)}
            </fieldset>
          )}
          <Field label="Confirmation / reference number (if any)"><input value={ref} onChange={(e) => setRef(e.target.value)} /></Field>
          <Field label="Note (e.g. confirmation e-mail received)"><textarea value={note} onChange={(e) => setNote(e.target.value)} /></Field>
          <button type="submit">Record outcome</button>
          {outErr && <Alert kind="error">{outErr}</Alert>}
        </form>
      )}
      <details><summary>History (immutable event log)</summary>
        {events.loading && !events.result && <Loading />}
        <ErrorBox result={events.result} />
        {events.result?.ok && <DataTable rows={eventRows(events.data ?? [])} columns={[
          { header: "#", render: (e) => e.seq }, { header: "When", render: (e) => e.when }, { header: "Event", render: (e) => e.event }, { header: "From", render: (e) => e.from },
          { header: "To", render: (e) => e.to }, { header: "By", render: (e) => e.by },
        ]} />}
      </details>
    </>
  );
}
