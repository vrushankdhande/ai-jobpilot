import { useEffect, useMemo, useState, type ClipboardEvent } from "react";
import { api } from "../api";
import { buildImportBody, importMessage } from "../lib/discovery";
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
    <details>
      <summary>1. 🔗 Open the Naukri search</summary>
      <p className="muted">Opens Naukri in a new tab with your search filled in. Then copy the results page into box 2. (Naukri may ignore a filter - set it on its own page if so.)</p>
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

type Row = PastedNaukriJob & { pick: boolean; link: string; result?: string };

/** Bulk paste: the user copies a whole Naukri results page themselves (Ctrl+A, Ctrl+C) and pastes it here. The text is split into jobs in the
 *  browser; the ticked ones are imported one by one through the normal import API, each scored straight away. Nothing contacts Naukri. */
export function NaukriBulkPaste({ onDone }: { onDone: () => void }) {
  const [text, setText] = useState("");
  const [links, setLinks] = useState<NaukriLink[]>([]);
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<string | null>(null);

  // the jobs are read as soon as something is pasted - no extra button
  useEffect(() => {
    setSummary(null);
    if (!text.trim()) { setRows([]); return; }
    const jobs = parseNaukriResults(text);
    const found = naukriLinksFor(jobs, links);
    setRows(jobs.map((j, i) => ({ ...j, pick: true, link: found[i] ?? "" })));
  }, [text, links]);
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
    <details>
      <summary>2. 📋 Paste the Naukri results here</summary>
      <p className="muted">On the Naukri results page press <b>Ctrl+A</b>, then <b>Ctrl+C</b>, and paste here (<b>Ctrl+V</b>). The jobs and their links are read automatically - then press Import.
        JobPilot never opens Naukri itself. A results page has only a short summary per job, so scores are rougher than with the full description.</p>
      <Field label="Paste the copied Naukri results page"><textarea style={{ minHeight: 140 }} value={text} onPaste={onPaste} onChange={(e) => setText(e.target.value)} /></Field>
      {text.trim() !== "" && <div className="muted" role="status">{links.length ? `🔗 ${links.length} job link(s) found in what you copied - they are filled in for you.` : "No job links came with the paste - you can add a job's link by hand."}</div>}
      {rows.length === 0 && text.trim() !== "" && <Alert kind="warning">No jobs found in what you pasted. Copy the whole Naukri <b>results</b> page (Ctrl+A, Ctrl+C) and paste again.</Alert>}
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
