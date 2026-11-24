import { useMemo, useState, type ClipboardEvent, type FormEvent } from "react";
import { api } from "../api";
import { buildImportBody, importMessage } from "../lib/discovery";
import { suggestTitle } from "../lib/linkedin";
import { naukriLinksFor, naukriLinksFromHtml, naukriSearchUrl, parseNaukriJobUrl, parseNaukriResults, type NaukriLink, type PastedNaukriJob } from "../lib/naukri";
import { Alert, DataTable, Field } from "./ui";

/** Builds a Naukri search link for YOU to open. This app never contacts Naukri (manual-only by policy: no scraping, no automated login). */
export function NaukriSearch() {
  const [keywords, setKeywords] = useState("");
  const [location, setLocation] = useState("");
  const [years, setYears] = useState("");
  const [posted, setPosted] = useState<"any" | "day" | "3days" | "week" | "15days" | "month">("any");
  const exp = years.trim() === "" ? null : Number(years);
  const url = useMemo(() => naukriSearchUrl({ keywords, location, experienceYears: exp, postedWithin: posted }), [keywords, location, exp, posted]);
  return (
    <details open>
      <summary>🔗 Search on Naukri (opens Naukri in a new tab)</summary>
      <p className="muted">Naukri does not allow automated collection, so JobPilot never contacts it. This only builds the search link; you open the jobs yourself, then copy a job you like into the quick import below. Naukri may ignore a filter - set it on its own page if so.</p>
      <div className="grid cols-3">
        <Field label="Naukri keywords"><input value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder="e.g. AI engineer" /></Field>
        <Field label="Naukri location"><input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="e.g. Pune" /></Field>
        <Field label="Naukri experience (years)"><input type="number" min={0} max={40} value={years} onChange={(e) => setYears(e.target.value)} placeholder="optional" /></Field>
        <Field label="Naukri posted within">
          <select value={posted} onChange={(e) => setPosted(e.target.value as typeof posted)}>
            <option value="any">Any time</option><option value="day">Last 1 day</option><option value="3days">Last 3 days</option>
            <option value="week">Last 7 days</option><option value="15days">Last 15 days</option><option value="month">Last 30 days</option>
          </select>
        </Field>
      </div>
      {url
        ? <a className="btn primary" href={url} target="_blank" rel="noopener noreferrer">Open Naukri search ↗</a>
        : <button disabled title="Enter keywords first">Open Naukri search ↗</button>}
    </details>
  );
}

/** Quick import of a job the user copied from Naukri: reads the job id from the pasted link and imports through the normal manual-import API. Nothing is fetched. */
export function NaukriQuickImport({ onDone }: { onDone: () => void }) {
  const blank = { link: "", text: "", title: "", company: "", loc: "" };
  const [v, setV] = useState(blank);
  const [titleTouched, setTitleTouched] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const ref = useMemo(() => parseNaukriJobUrl(v.link), [v.link]);
  const ready = Boolean(ref && v.title.trim() && v.company.trim() && v.text.trim().length >= 50);

  function onText(text: string) {
    setV({ ...v, text, title: titleTouched || v.title ? v.title : suggestTitle(text) });
  }
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ref || !ready) return;
    setBusy(true); setMsg(null);
    const res = await api.post("/api/jobs/import", { json: buildImportBody(ref.canonicalUrl, { job_title: v.title.trim(), company_name: v.company.trim(), location: v.loc.trim(), job_description: v.text.trim() }) });
    setBusy(false);
    if (!res.ok) { setMsg({ ok: false, text: res.error ?? "Failed" }); return; }
    setMsg({ ok: true, text: importMessage(res.data.action, res.data.match) });
    setV(blank); setTitleTouched(false);
    onDone();
  }
  return (
    <details open>
      <summary>⚡ Quick import from Naukri (paste)</summary>
      <p className="muted">On the Naukri job page, copy the link and the job text, paste them here, and check the title and company. The link is stored for reference (without tracking parameters) and is never opened or fetched by this app.</p>
      <form onSubmit={submit} aria-label="Quick import from Naukri">
        <Field label="Naukri job link"><input value={v.link} onChange={(e) => setV({ ...v, link: e.target.value })} placeholder="https://www.naukri.com/job-listings-…" /></Field>
        {v.link.trim() !== "" && (ref
          ? <div className="muted" role="status">✅ Naukri job <b>{ref.jobId}</b> - stored as {ref.canonicalUrl}</div>
          : <Alert kind="warning">That does not look like a Naukri job link. Open the job itself on Naukri and copy its address (it contains <code>/job-listings-</code>).</Alert>)}
        <Field label="Paste the Naukri job text (title, company, description…)"><textarea style={{ minHeight: 160 }} value={v.text} onChange={(e) => onText(e.target.value)} /></Field>
        <div className="grid cols-3">
          <Field label={`Naukri job title *${v.title && !titleTouched ? " (suggested - check it)" : ""}`}><input value={v.title} onChange={(e) => { setTitleTouched(true); setV({ ...v, title: e.target.value }); }} /></Field>
          <Field label="Naukri company *"><input value={v.company} onChange={(e) => setV({ ...v, company: e.target.value })} /></Field>
          <Field label="Naukri job location"><input value={v.loc} onChange={(e) => setV({ ...v, loc: e.target.value })} /></Field>
        </div>
        <button type="submit" className="primary" disabled={!ready || busy}>Import Naukri job</button>
        {!ready && <span className="muted"> Needs a Naukri job link, a title, a company and at least 50 characters of job text.</span>}
      </form>
      {msg && <Alert kind={msg.ok ? "success" : "error"}>{msg.text}</Alert>}
    </details>
  );
}

type Row = PastedNaukriJob & { pick: boolean; link: string; result?: string };

/** Bulk paste: the user copies a whole Naukri results page themselves (Ctrl+A, Ctrl+C) and pastes it here. The text is split into jobs in the
 *  browser; the ticked ones are imported one by one through the normal import API, each scored straight away. Nothing contacts Naukri. */
export function NaukriBulkPaste({ onDone }: { onDone: () => void }) {
  const [text, setText] = useState("");
  const [links, setLinks] = useState<NaukriLink[]>([]);
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<string | null>(null);

  function read() {
    setSummary(null);
    const jobs = parseNaukriResults(text);
    const found = naukriLinksFor(jobs, links);
    setRows(jobs.map((j, i) => ({ ...j, pick: true, link: found[i] ?? "" })));
  }
  function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    setLinks(naukriLinksFromHtml(e.clipboardData.getData("text/html") ?? ""));   // the job links that came with the copy; the text is pasted as usual
  }
  const set = (i: number, patch: Partial<Row>) => setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  const picked = rows.filter((r) => r.pick && !r.result);

  async function importAll() {
    setBusy(true); setSummary(null);
    let ok = 0, dup = 0, bad = 0;
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      if (!r.pick || r.result) continue;
      const ref = parseNaukriJobUrl(r.link);
      const body = { ...buildImportBody(ref?.canonicalUrl ?? "", { job_title: r.title, company_name: r.company, location: r.location ?? "", job_description: r.text,
        experience_requirement: r.experience ?? "", salary: r.salary ?? "", posted_date: r.postedDate ?? "" }), ...(ref ? {} : { source: "naukri" }) };
      const res = await api.post("/api/jobs/import", { json: body });
      if (!res.ok) { bad++; set(i, { result: `❌ ${res.error ?? "Failed"}` }); continue; }
      const action = res.data.action as string;
      if (action === "insert") ok++; else dup++;
      set(i, { result: (action === "insert" ? "✅ " : "↺ ") + importMessage(action, res.data.match) });
    }
    setBusy(false);
    setSummary(`${ok} new job(s) imported, ${dup} already known (not added twice), ${bad} failed.`);
    if (ok || dup) onDone();
  }

  return (
    <details open>
      <summary>📋 Bulk paste from a Naukri results page</summary>
      <p className="muted">Open your search on Naukri, press <b>Ctrl+A</b> then <b>Ctrl+C</b> on the results page, and paste it here. JobPilot only splits the text you copied into jobs - it never opens or reads Naukri.
        The job links that come with your copy are filled in automatically. A results page shows a short summary per job, so scores are rougher than with the full description.</p>
      <Field label="Paste the copied Naukri results page"><textarea style={{ minHeight: 140 }} value={text} onPaste={onPaste} onChange={(e) => setText(e.target.value)} /></Field>
      {text.trim() !== "" && <div className="muted" role="status">{links.length ? `🔗 ${links.length} job link(s) found in what you copied - they are filled in for you.` : "No job links came with the paste - you can add a job's link by hand."}</div>}
      <button type="button" onClick={read} disabled={!text.trim() || busy}>Read jobs from the pasted text</button>
      {rows.length === 0 && text.trim() !== "" && summary === null && <span className="muted"> Press the button to see the jobs found in the text.</span>}
      {rows.length > 0 && (
        <>
          <p><b>{rows.length}</b> job(s) found. Untick any you do not want, check titles and companies, then import.</p>
          <DataTable rows={rows} rowKey={(r, i) => `${i}-${r.title}`} columns={[
            { header: "Import", render: (r) => <input type="checkbox" aria-label={`Import ${r.title} at ${r.company}`} checked={r.pick} disabled={Boolean(r.result)} onChange={(e) => set(rows.indexOf(r), { pick: e.target.checked })} /> },
            { header: "Title", render: (r) => r.title }, { header: "Company", render: (r) => r.company },
            { header: "Location", render: (r) => r.location ?? "—" }, { header: "Experience", render: (r) => r.experience ?? "—" },
            { header: "Posted", render: (r) => r.posted ?? "—" },
            { header: "Job link (optional)", render: (r) => <input aria-label={`Link for ${r.title}`} value={r.link} placeholder="https://www.naukri.com/job-listings-…" disabled={Boolean(r.result)}
                onChange={(e) => set(rows.indexOf(r), { link: e.target.value })} /> },
            { header: "Result", render: (r) => r.result ?? "" },
          ]} />
          <button type="button" className="primary" onClick={() => void importAll()} disabled={busy || picked.length === 0}>{busy ? "Importing…" : `Import ${picked.length} selected job(s)`}</button>
          {rows.some((r) => r.link.trim() && !parseNaukriJobUrl(r.link)) && <Alert kind="warning">A link that is not a Naukri job link (with <code>/job-listings-</code>) is ignored; the job is still imported without it.</Alert>}
        </>
      )}
      {summary && <Alert kind="success">{summary}</Alert>}
    </details>
  );
}
