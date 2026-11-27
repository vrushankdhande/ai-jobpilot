import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { autofillPayload, autofillSummary, bookmarkletHref, type AutofillData } from "../lib/autofill";
import { Alert } from "./ui";

/** Fill an application form in YOUR browser: copy this job's verified details, open the form, click the JobPilot Autofill bookmark and paste.
 *  JobPilot never submits: you check the form, attach the resume, complete any CAPTCHA and press Submit yourself. */
export function AutofillHelper({ jobId, applyUrl }: { jobId: string; applyUrl: string | null }) {
  const link = useRef<HTMLAnchorElement>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [data, setData] = useState<AutofillData | null>(null);
  const [manual, setManual] = useState<string | null>(null);
  useEffect(() => { link.current?.setAttribute("href", bookmarkletHref()); }, []);
  useEffect(() => { setMsg(null); setData(null); setManual(null); }, [jobId]);

  async function copy() {
    setBusy(true); setMsg(null); setManual(null);
    const r = await api.get<AutofillData>(`/api/applications/autofill/${jobId}`);
    setBusy(false);
    if (!r.ok || !r.data) { setMsg({ ok: false, text: r.error ?? "Could not load your details" }); return; }
    setData(r.data);
    const text = autofillPayload(r.data);
    try {
      await navigator.clipboard.writeText(text);
      setMsg({ ok: true, text: `${autofillSummary(r.data)} Now open the application form and click the JobPilot Autofill bookmark.` });
    } catch {
      setManual(text);                                     // clipboard blocked: show it to copy by hand
      setMsg({ ok: true, text: `${autofillSummary(r.data)} Your browser did not allow copying - select the text below and copy it (Ctrl+C).` });
    }
  }

  return (
    <details className="autofill">
      <summary>⚡ Autofill this application in your browser</summary>
      <ol>
        <li>Once: drag this button to your browser&apos;s bookmarks bar (Ctrl+Shift+B shows the bar) →{" "}
          <a ref={link} className="btn" draggable onClick={(e) => { e.preventDefault(); setMsg({ ok: true, text: "Drag the button to your bookmarks bar (do not click it here)." }); }}>⚡ JobPilot Autofill</a>
        </li>
        <li><button className="primary" onClick={() => void copy()} disabled={busy}>{busy ? "Loading…" : "📋 Copy autofill data"}</button></li>
        <li>{applyUrl ? <a href={applyUrl} target="_blank" rel="noopener noreferrer">Open the application form ↗</a> : "Open the application form"}, click the <b>JobPilot Autofill</b> bookmark, paste (Ctrl+V) and press OK.</li>
        <li>Check every field (green = filled, amber = check, red = answer yourself), attach your resume, complete any CAPTCHA and press <b>Submit yourself</b>. Then flip the <b>Applied</b> switch.</li>
      </ol>
      <p className="muted">Fills only empty fields, from your reviewed profile and this job&apos;s approved cover letter and answers. It never presses Submit, never touches CAPTCHAs, uploads,
        consent or diversity questions, and sends nothing anywhere - the data goes from your clipboard into the form you are looking at.</p>
      {msg && <Alert kind={msg.ok ? "success" : "error"}>{msg.text}</Alert>}
      {data && data.missing.length > 0 && <p className="muted">Not in your profile or preferences (fill these yourself if asked): {data.missing.join(", ")}.</p>}
      {data && data.answers.length === 0 && <p className="muted">No approved screening answers or cover letter for this job yet - generate and approve them in Document Generation to have them filled too.</p>}
      {manual && <textarea readOnly aria-label="Autofill data to copy" value={manual} style={{ height: 80 }} onFocus={(e) => e.currentTarget.select()} />}
    </details>
  );
}
