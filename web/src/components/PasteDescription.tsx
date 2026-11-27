import { useState } from "react";
import { api } from "../api";
import { Alert, Field } from "./ui";

export interface PasteTarget { id: string; title: string; company: string; url: string | null }

/** Add the description of a job that came without one (alert e-mails): open the job, copy its description, paste it here. The job is then
 *  scored straight away. JobPilot never opens the job page itself - the text comes only from what you paste. */
export function PasteDescription({ job, onDone, onClose }: { job: PasteTarget; onDone: () => void; onClose: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const tooShort = text.trim().length < 200;

  async function save() {
    setBusy(true); setMsg(null);
    const r = await api.post<any>(`/api/jobs/${job.id}/description`, { json: { job_description: text.trim() } });
    setBusy(false);
    if (!r.ok) { setMsg({ ok: false, text: r.error ?? "Not saved" }); return; }        // what was pasted stays in the box
    const m = r.data?.match;
    setMsg({ ok: true, text: m ? `Scored: ${m.match_score} / 100 · ${m.classification} · eligibility ${m.eligibility}.` : "Description saved. It could not be scored yet (is your profile reviewed?)." });
    setText("");
    onDone();
  }

  return (
    <div className="paste-desc" role="region" aria-label={`Paste the description of ${job.title}`}>
      <p><b>Add the description:</b> {job.title}{job.company ? ` at ${job.company}` : ""}{" "}
        {job.url && <a href={job.url} target="_blank" rel="noopener noreferrer">Open job ↗</a>}
        <button className="link-btn" onClick={onClose} aria-label="Close">✕</button></p>
      <p className="muted">On the job page select the description (or press Ctrl+A), copy it (Ctrl+C) and paste it below. It is checked against your resume right away.</p>
      <Field label="Job description"><textarea style={{ height: 180 }} value={text} onChange={(e) => setText(e.target.value)} /></Field>
      <button className="primary" disabled={busy || tooShort} onClick={() => void save()}>{busy ? "Scoring…" : "Save & score"}</button>
      {tooShort && text.trim() !== "" && <span className="muted"> Paste the whole description (at least 200 characters).</span>}
      {msg && <Alert kind={msg.ok ? "success" : "error"}>{msg.text}</Alert>}
    </div>
  );
}
