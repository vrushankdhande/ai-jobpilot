import { useEffect, useMemo, useState } from "react";
import { api, type Result } from "../api";
import { PageHeader } from "../components/Layout";
import { Alert, Check, ConfirmButton, DataTable, Empty, ErrorBox, Field, Loading, MetricRow, Paginator, useFlash } from "../components/ui";
import { fmtDt } from "../format";
import { useFetch } from "../hooks";
import { addBody, badge, editBody, editFormOf, emptyAddForm, INTERVIEW_STATUS, KINDS, listParams, OUTCOME, OUTCOMES, STATUSES, type AddForm, type EditForm } from "../lib/interviews";

const PAGE_SIZE = 10;

/** A rejected save is shown as 'Not saved: …' and keeps everything the user typed. */
function NotSaved({ result }: { result: Result | null }) {
  if (!result || result.ok) return null;
  return <Alert kind="error">Not saved: {result.error}{result.problems.length > 0 && <ul>{result.problems.map((p) => <li key={p}>{p}</li>)}</ul>}</Alert>;
}

export default function InterviewTracker() {
  const { setFlash } = useFlash();
  const [version, setVersion] = useState(0);                       // bumped after every successful change: lists re-fetch
  const refresh = () => setVersion((v) => v + 1);
  const rem = useFetch("/api/interviews/reminders", { v: version });
  const up = useFetch("/api/interviews/upcoming", { hours: 24 * 30, v: version });
  const all = useFetch("/api/interviews", { limit: 1, v: version });

  return (
    <>
      <PageHeader icon="🗓️" title="Interview Tracker" />
      {rem.data?.map((i: any) => (
        <Alert key={i.interview_id ?? `${i.company_name}-${i.scheduled_at}`} kind="warning">⏰ <b>{i.company_name} — {i.job_title} ({i.round})</b> starts {fmtDt(i.scheduled_at)} (UTC).{i.meeting_link ? ` Link: ${i.meeting_link}` : ""}</Alert>
      ))}
      <MetricRow items={[["Upcoming (30 days)", up.result?.ok ? up.data.length : null], ["All interviews", all.result?.ok ? all.data.total : null], ["Reminders due now", rem.result?.ok ? rem.data.length : null, "Inside their own reminder window"]]} />

      <h2>Upcoming</h2>
      {up.loading && !up.result ? <Loading /> : up.result && !up.result.ok ? <ErrorBox result={up.result} />
        : up.data?.length ? <DataTable rows={up.data as any[]} columns={[
          { header: "When (UTC)", render: (i) => fmtDt(i.scheduled_at) }, { header: "Company", render: (i) => i.company_name }, { header: "Role", render: (i) => i.job_title },
          { header: "Round", render: (i) => i.round }, { header: "Type", render: (i) => i.kind }, { header: "Link", render: (i) => i.meeting_link || "" }]} />
          : <Empty message="No interviews scheduled in the next 30 days." />}

      <AddInterview onAdded={(text) => { setFlash(text); refresh(); }} />
      <AllInterviews version={version} onChanged={(text) => { setFlash(text); refresh(); }} />
    </>
  );
}

function AddInterview({ onAdded }: { onAdded: (flash: string) => void }) {
  const apps = useFetch("/api/applications/page", { limit: 200 });
  const [f, setF] = useState<AddForm>(() => emptyAddForm());
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<Result | null>(null);
  const set = <K extends keyof AddForm>(k: K, v: AddForm[K]) => setF((cur) => ({ ...cur, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const r = await api.post("/api/interviews", { json: addBody(f) });
    setBusy(false);
    setRes(r);
    if (r.ok) {                                                  // cleared ONLY after a successful save; a rejected entry keeps what was typed
      setF(emptyAddForm());
      onAdded(`Interview added: ${f.company} — ${f.title} (${f.round}).`);
    }
  }

  return (
    <>
      <h2>Add an interview</h2>
      <form onSubmit={submit} aria-label="Add interview">
        <Field label="Linked application (optional)">
          <select value={f.linkedApp} onChange={(e) => set("linkedApp", e.target.value)}>
            <option value="">— not linked to an application —</option>
            {apps.data?.items?.map((a: any) => <option key={a.application_id} value={a.application_id}>{`${a.job_title || "(untitled)"} - ${a.company_name || ""} [${a.state}]`}</option>)}
          </select>
        </Field>
        <div className="grid cols-3">
          <Field label="Company *"><input value={f.company} onChange={(e) => set("company", e.target.value)} /></Field>
          <Field label="Job title *"><input value={f.title} onChange={(e) => set("title", e.target.value)} /></Field>
          <Field label="Round"><input value={f.round} onChange={(e) => set("round", e.target.value)} /></Field>
        </div>
        <div className="grid cols-4">
          <Field label="Date *"><input type="date" value={f.date} onChange={(e) => set("date", e.target.value)} /></Field>
          <Field label="Time * (local)"><input type="time" value={f.time} onChange={(e) => set("time", e.target.value)} /></Field>
          <Field label="Type"><select value={f.kind} onChange={(e) => set("kind", e.target.value)}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select></Field>
          <Field label="Duration (min)"><input type="number" min={5} max={600} value={f.duration} onChange={(e) => set("duration", e.target.value)} /></Field>
        </div>
        <Field label="Meeting link (https)"><input value={f.link} onChange={(e) => set("link", e.target.value)} /></Field>
        <div className="grid cols-2">
          <Field label="Interviewers"><input value={f.interviewers} onChange={(e) => set("interviewers", e.target.value)} /></Field>
          <Field label="Remind me (minutes before)"><input type="number" min={0} max={10080} value={f.remind} onChange={(e) => set("remind", e.target.value)} /></Field>
        </div>
        <Field label="Preparation notes"><textarea value={f.notes} onChange={(e) => set("notes", e.target.value)} /></Field>
        <button type="submit" className="primary" disabled={busy}>➕ Add interview</button>
      </form>
      <NotSaved result={res} />
    </>
  );
}

function AllInterviews({ version, onChanged }: { version: number; onChanged: (flash: string) => void }) {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("Any");
  const [outcome, setOutcome] = useState("Any");
  const [newest, setNewest] = useState(false);
  const [page, setPage] = useState(1);
  const [pick, setPick] = useState("");
  const params = useMemo(() => listParams({ q, status, outcome, newest }), [q, status, outcome, newest]);
  const probe = useFetch("/api/interviews", { ...params, limit: 1, v: version });
  const total: number = probe.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const cur = Math.min(Math.max(1, page), pages);
  useEffect(() => { if (cur !== page) setPage(cur); }, [cur, page]);          // the page shrinks back when the result set does
  const list = useFetch(probe.result?.ok ? "/api/interviews" : null, { ...params, limit: PAGE_SIZE, skip: (cur - 1) * PAGE_SIZE, v: version });
  const rows: any[] = list.data?.items ?? [];
  useEffect(() => { if (rows.length && !rows.some((r) => r.interview_id === pick)) setPick(rows[0].interview_id); }, [rows, pick]);
  const selected = rows.find((r) => r.interview_id === pick);
  const onFilter = (fn: () => void) => { fn(); setPage(1); };

  return (
    <>
      <h2>All interviews</h2>
      <div className="grid cols-4">
        <Field label="Search company / role"><input value={q} onChange={(e) => onFilter(() => setQ(e.target.value))} /></Field>
        <Field label="Status"><select value={status} onChange={(e) => onFilter(() => setStatus(e.target.value))}>{["Any", ...STATUSES].map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Outcome"><select value={outcome} onChange={(e) => onFilter(() => setOutcome(e.target.value))}>{["Any", ...OUTCOMES].map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Check label="Newest first" checked={newest} onChange={(v) => onFilter(() => setNewest(v))} />
      </div>
      {probe.loading && !probe.result ? <Loading /> : null}
      <ErrorBox result={probe.result} />
      <ErrorBox result={list.result} />
      {probe.result?.ok && <Paginator total={total} size={PAGE_SIZE} page={cur} onPage={setPage} />}
      {probe.result?.ok && list.result?.ok && !rows.length && <Empty message="No interviews match." hint="Add your first interview above." />}
      {rows.length > 0 && (
        <>
          <DataTable rows={rows} rowKey={(r) => r.interview_id} columns={[
            { header: "When (UTC)", render: (i) => fmtDt(i.scheduled_at) }, { header: "Company", render: (i) => i.company_name }, { header: "Role", render: (i) => i.job_title },
            { header: "Round", render: (i) => i.round }, { header: "Status", render: (i) => badge(INTERVIEW_STATUS, i.status) }, { header: "Outcome", render: (i) => badge(OUTCOME, i.outcome) }]} />
          <Field label="Open an interview">
            <select value={pick} onChange={(e) => setPick(e.target.value)}>
              {rows.map((i) => <option key={i.interview_id} value={i.interview_id}>{`${i.company_name} — ${i.job_title} (${i.round}) ${fmtDt(i.scheduled_at, false)}`}</option>)}
            </select>
          </Field>
          {selected && <EditInterview key={selected.interview_id + String(selected.updated_at ?? "")} cur={selected} onChanged={onChanged} />}
        </>
      )}
    </>
  );
}

function EditInterview({ cur, onChanged }: { cur: any; onChanged: (flash: string) => void }) {
  const original = useMemo(() => editFormOf(cur), [cur]);
  const [f, setF] = useState<EditForm>(original);
  const [res, setRes] = useState<Result | null>(null);
  const set = <K extends keyof EditForm>(k: K, v: EditForm[K]) => setF((c) => ({ ...c, [k]: v }));

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const r = await api.put(`/api/interviews/${cur.interview_id}`, { json: editBody(f, original) });
    setRes(r);
    if (r.ok) onChanged("Interview updated.");
  }
  async function remove() {
    const r = await api.del(`/api/interviews/${cur.interview_id}`);
    setRes(r);
    if (r.ok) onChanged("Interview deleted.");
  }

  return (
    <>
      <form onSubmit={save} aria-label="Edit interview">
        <p className="muted">Times are stored in UTC; enter changes in your local time zone.</p>
        <div className="grid cols-3">
          <Field label="Status"><select value={f.status} onChange={(e) => set("status", e.target.value)}>{STATUSES.map((s) => <option key={s}>{s}</option>)}</select></Field>
          <Field label="Outcome"><select value={f.outcome} onChange={(e) => set("outcome", e.target.value)}>{OUTCOMES.map((s) => <option key={s}>{s}</option>)}</select></Field>
          <Field label="Type"><select value={f.kind} onChange={(e) => set("kind", e.target.value)}>{KINDS.map((s) => <option key={s}>{s}</option>)}</select></Field>
        </div>
        <div className="grid cols-2">
          <Field label="Reschedule: date"><input type="date" value={f.date} onChange={(e) => set("date", e.target.value)} /></Field>
          <Field label="Reschedule: time (UTC as shown)"><input type="time" value={f.time} onChange={(e) => set("time", e.target.value)} /></Field>
        </div>
        <Field label="Meeting link"><input value={f.link} onChange={(e) => set("link", e.target.value)} /></Field>
        <Field label="Preparation notes"><textarea value={f.prep} onChange={(e) => set("prep", e.target.value)} /></Field>
        <Field label="Feedback / what was asked"><textarea value={f.feedback} onChange={(e) => set("feedback", e.target.value)} /></Field>
        <button type="submit" className="primary">💾 Save changes</button>
      </form>
      <NotSaved result={res} />
      {cur.history?.length > 0 && (
        <details><summary>Change history</summary>
          <DataTable rows={[...cur.history].reverse()} columns={[{ header: "When", render: (h: any) => fmtDt(h.at) }, { header: "Changed", render: (h: any) => (h.changed ?? []).join(", ") }]} />
        </details>
      )}
      <ConfirmButton label="🗑️ Delete this interview" question="Delete this interview record permanently?" onConfirm={remove} />
    </>
  );
}
