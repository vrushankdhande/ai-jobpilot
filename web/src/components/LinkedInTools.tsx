import { useEffect, useMemo, useState, type ClipboardEvent, type FormEvent } from "react";
import { api } from "../api";
import { useFetch } from "../hooks";
import { fmtDt } from "../format";
import { buildImportBody, importMessage } from "../lib/discovery";
import { linkedinSearchUrl, parseLinkedInJobUrl, suggestTitle } from "../lib/linkedin";
import { attachLinks, jobLinksFromHtml, linkedinJobUrl, parseLinkedInResults, type JobLink, type PastedLinkedInJob } from "../lib/linkedinPaste";
import { Alert, Check, DataTable, Empty, ErrorBox, Field, Loading } from "./ui";

const MODES = ["remote", "hybrid", "onsite"] as const;

/** Builds a LinkedIn search link for YOU to open. This app never contacts LinkedIn (manual-only by policy: no scraping, no automated login). */
export interface LinkedInSuggestions { profile_available: boolean; current_title: string | null; titles: string[]; skills: string[]; location: string | null; experience_years: number | null }

export function LinkedInSearch({ suggestions }: { suggestions?: LinkedInSuggestions }) {
  const [keywords, setKeywords] = useState("");
  const [location, setLocation] = useState("");
  const [touched, setTouched] = useState({ k: false, l: false });
  // Defaults come from your own reviewed profile (via this app's backend); anything you type is never overwritten.
  useEffect(() => {
    if (!suggestions?.profile_available) return;
    if (!touched.k && suggestions.current_title) setKeywords(suggestions.current_title);
    if (!touched.l && suggestions.location) setLocation(suggestions.location);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suggestions]);
  const [modes, setModes] = useState<Array<(typeof MODES)[number]>>([]);
  const [posted, setPosted] = useState<"any" | "day" | "week" | "month">("any");
  const url = useMemo(() => linkedinSearchUrl({ keywords, location, workModes: modes, postedWithin: posted, sortRecent: true }), [keywords, location, modes, posted]);
  const toggle = (m: (typeof MODES)[number]) => setModes(modes.includes(m) ? modes.filter((x) => x !== m) : [...modes, m]);
  return (
    <details open>
      <summary>🔗 Search on LinkedIn (opens LinkedIn in a new tab)</summary>
      <p className="muted">LinkedIn does not allow automated collection, so JobPilot never contacts it. This only builds the search link; you open the jobs yourself, then copy a job you like into the quick import below.</p>
      <div className="grid cols-3">
        <Field label="Keywords"><input value={keywords} onChange={(e) => { setTouched({ ...touched, k: true }); setKeywords(e.target.value); }} placeholder="e.g. AI engineer" /></Field>
        <Field label="Location"><input value={location} onChange={(e) => { setTouched({ ...touched, l: true }); setLocation(e.target.value); }} placeholder="e.g. Pune, India" /></Field>
        <Field label="Posted within">
          <select value={posted} onChange={(e) => setPosted(e.target.value as typeof posted)}>
            <option value="any">Any time</option><option value="day">Past 24 hours</option><option value="week">Past week</option><option value="month">Past month</option>
          </select>
        </Field>
      </div>
      {suggestions?.profile_available
        ? <p className="muted">From your profile - titles (click to use): {suggestions.titles.map((t) => <button key={t} type="button" className="chip" onClick={() => { setTouched({ ...touched, k: true }); setKeywords(t); }}>{t}</button>)}
          {suggestions.location ? ` · location: ${suggestions.location} (change it above)` : " · no location in your profile - type one above"}</p>
        : suggestions && <p className="muted">Upload and review a resume to get title and location defaults from your profile.</p>}
      <fieldset><legend>Work mode</legend>{MODES.map((m) => <Check key={m} label={m} checked={modes.includes(m)} onChange={() => toggle(m)} />)}</fieldset>
      {url
        ? <a className="btn primary" href={url} target="_blank" rel="noopener noreferrer">Open LinkedIn search ↗</a>
        : <button disabled title="Enter keywords first">Open LinkedIn search ↗</button>}
    </details>
  );
}

/** Quick import of a job the user copied from LinkedIn: reads the job id from the pasted link and imports through the normal manual-import API. Nothing is fetched. */
export function LinkedInQuickImport({ onDone }: { onDone: () => void }) {
  const blank = { link: "", text: "", title: "", company: "", loc: "" };
  const [v, setV] = useState(blank);
  const [titleTouched, setTitleTouched] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const ref = useMemo(() => parseLinkedInJobUrl(v.link), [v.link]);
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
      <summary>⚡ Quick import from LinkedIn (paste)</summary>
      <p className="muted">On the LinkedIn job page, copy the link and the job text, paste them here, and check the title and company. The link is stored for reference and is never opened or fetched by this app.</p>
      <form onSubmit={submit} aria-label="Quick import from LinkedIn">
        <Field label="LinkedIn job link"><input value={v.link} onChange={(e) => setV({ ...v, link: e.target.value })} placeholder="https://www.linkedin.com/jobs/view/…  or a search link containing currentJobId=…" /></Field>
        {v.link.trim() !== "" && (ref
          ? <div className="muted" role="status">✅ LinkedIn job <b>{ref.jobId}</b> - stored as {ref.canonicalUrl}</div>
          : <Alert kind="warning">That does not look like a LinkedIn job link. Use the job page link, or a search link that contains <code>currentJobId=</code>.</Alert>)}
        <Field label="Paste the LinkedIn job text (title, company, description…)"><textarea style={{ minHeight: 160 }} value={v.text} onChange={(e) => onText(e.target.value)} /></Field>
        <div className="grid cols-3">
          <Field label={`LinkedIn job title *${v.title && !titleTouched ? " (suggested - check it)" : ""}`}><input value={v.title} onChange={(e) => { setTitleTouched(true); setV({ ...v, title: e.target.value }); }} /></Field>
          <Field label="LinkedIn company *"><input value={v.company} onChange={(e) => setV({ ...v, company: e.target.value })} /></Field>
          <Field label="LinkedIn job location"><input value={v.loc} onChange={(e) => setV({ ...v, loc: e.target.value })} /></Field>
        </div>
        <button type="submit" className="primary" disabled={!ready || busy}>Import LinkedIn job</button>
        {!ready && <span className="muted"> Needs a LinkedIn job link, a title, a company and at least 50 characters of job text.</span>}
      </form>
      {msg && <Alert kind={msg.ok ? "success" : "error"}>{msg.text}</Alert>}
    </details>
  );
}

interface LinkedInRow {
  job_id: string; job_title: string | null; company_name: string | null; location: string | null; experience_min: number | null; experience_max: number | null
  posted_date: string | null; discovered_at: string; match_score: number | null; eligibility: string | null; job_url: string | null; application_status: string
}
const expText = (r: LinkedInRow) => (r.experience_min == null ? "Not stated" : r.experience_max != null && r.experience_max !== r.experience_min ? `${r.experience_min}-${r.experience_max} yrs` : `${r.experience_min}+ yrs`);

/** The LinkedIn jobs you imported, with their latest match score, eligibility and application status. Reads only this app's own database. */
export function LinkedInJobsTable({ reloadKey = 0 }: { reloadKey?: number }) {
  const t = useFetch<{ total: number; items: LinkedInRow[] }>("/api/jobs/linkedin/table", { k: reloadKey });
  return (
    <details open>
      <summary>📋 Your LinkedIn jobs</summary>
      {t.loading && !t.data && <Loading />}
      <ErrorBox result={t.result} />
      {t.data && t.data.items.length === 0 && <Empty message="No LinkedIn jobs yet." hint="Open a LinkedIn search above, then paste a job you like into the quick import. Imported jobs are scored straight away and also appear on the Job Matching page." />}
      {t.data && t.data.items.length > 0 && (
        <DataTable rows={t.data.items} rowKey={(r) => r.job_id} columns={[
          { header: "Job title", render: (r) => r.job_title ?? "—" },
          { header: "Company", render: (r) => r.company_name ?? "—" },
          { header: "Location", render: (r) => r.location ?? "—" },
          { header: "Experience", render: expText },
          { header: "Posted", render: (r) => (r.posted_date ? fmtDt(r.posted_date) : "Not stated") },
          { header: "Score", render: (r) => (r.match_score == null ? "Not scored" : `${r.match_score}%`) },
          { header: "Eligibility", render: (r) => r.eligibility ?? "Not scored" },
          { header: "Link", render: (r) => <JobLinkCell url={r.job_url} title={r.job_title} company={r.company_name} /> },
          { header: "Application", render: (r) => r.application_status.replaceAll("_", " ").toLowerCase() },
        ]} />
      )}
    </details>
  );
}

/** The exact LinkedIn job link when we have it, otherwise a LinkedIn search for "title company" that YOU open (the job is usually the first result). */
export function JobLinkCell({ url, title, company }: { url: string | null; title: string | null; company: string | null }) {
  if (url) return <a href={url} target="_blank" rel="noopener noreferrer">Open on LinkedIn ↗</a>;
  const search = linkedinSearchUrl({ keywords: `${title ?? ""} ${company ?? ""}` });
  return search
    ? <a href={search} target="_blank" rel="noopener noreferrer" title="No exact link was copied - this searches LinkedIn for the title and company">Find on LinkedIn ↗</a>
    : <>—</>;
}

type LiRow = PastedLinkedInJob & { pick: boolean; tooOld: boolean; result?: string };

/** Bulk paste of a LinkedIn search page the user copied themselves (Ctrl+A, Ctrl+C). The text is split into jobs in the browser; the job links
 *  come from the HTML copy the browser puts on the clipboard. Only the jobs list and the open job's description are read - messages,
 *  notifications and names on the page are ignored, and the pasted text is never stored or sent. Nothing contacts LinkedIn. */
export function LinkedInBulkPaste({ onDone }: { onDone: () => void }) {
  const prefs = useFetch<{ preferences?: { posted_within_days?: number | null } }>("/api/settings");
  const maxAge = prefs.data?.preferences?.posted_within_days || null;
  const [text, setText] = useState("");
  const [links, setLinks] = useState<JobLink[]>([]);
  const [address, setAddress] = useState("");
  const [rows, setRows] = useState<LiRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<string | null>(null);

  function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    setLinks(jobLinksFromHtml(e.clipboardData.getData("text/html") ?? ""));   // the text itself is pasted by the browser as usual
  }
  function read() {
    setSummary(null);
    const jobs = attachLinks(parseLinkedInResults(text), links, address);
    setRows(jobs.map((j) => {
      const tooOld = maxAge !== null && j.ageDays !== null && j.ageDays > maxAge;
      return { ...j, tooOld, pick: !tooOld };
    }));
  }
  const set = (i: number, patch: Partial<LiRow>) => setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  const picked = rows.filter((r) => r.pick && !r.result);
  const withLink = rows.filter((r) => r.jobId).length;

  async function importAll() {
    setBusy(true); setSummary(null);
    let ok = 0, dup = 0, bad = 0, applied = 0;
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      if (!r.pick || r.result) continue;
      const url = linkedinJobUrl(r.jobId);
      const job: Record<string, string> = { job_title: r.title, company_name: r.company, location: r.location ?? "", job_description: r.description ?? "",
        posted_date: r.postedDate ?? "", salary: r.salary ?? "", work_mode: r.workMode ?? "" };
      const res = await api.post("/api/jobs/import", { json: { ...buildImportBody(url ?? "", job), ...(url ? {} : { source: "linkedin" }) } });
      if (!res.ok) { bad++; set(i, { result: `❌ ${res.error ?? "Failed"}` }); continue; }
      const action = res.data.action as string;
      if (action === "insert") ok++; else dup++;
      let note = (action === "insert" ? "✅ " : "↺ ") + importMessage(action, res.data.match);
      if (!res.data.match && !r.description) note += " Not scored yet: paste its description into the quick import to score it.";
      const jobId = res.data.job?.job_id;
      if (r.applied && jobId) {
        const a = await api.post("/api/applications/record-applied", { json: { job_id: jobId } });
        if (a.ok) { applied++; note += " Marked as applied."; } else note += " Could not mark it applied - use the Applied switch.";
      }
      set(i, { result: note });
    }
    setBusy(false);
    setSummary(`${ok} new job(s) imported, ${dup} already known (not added twice), ${bad} failed${applied ? `, ${applied} marked as applied` : ""}.`);
    if (ok || dup) onDone();
  }

  return (
    <details open>
      <summary>📋 Bulk paste from a LinkedIn search page</summary>
      <p className="muted">Open your search on LinkedIn, press <b>Ctrl+A</b> then <b>Ctrl+C</b>, and paste it here. JobPilot only reads what you copied - it never opens or reads LinkedIn. Only the jobs list and the
        open job's description are used: your messages and notifications on the page are ignored, and the pasted text is not stored. LinkedIn's list has no descriptions, so those jobs are saved unscored; the job you had open is scored straight away.</p>
      <Field label="Paste the copied LinkedIn search page"><textarea style={{ minHeight: 140 }} value={text} onPaste={onPaste} onChange={(e) => setText(e.target.value)} /></Field>
      <Field label="Page address (optional - gives the open job its exact link)"><input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="https://www.linkedin.com/jobs/search/?currentJobId=…" /></Field>
      {text.trim() !== "" && (
        <div className="muted" role="status">
          {links.length ? `🔗 ${new Set(links.map((l) => l.id)).size} job link(s) found in what you copied.` : "No job links came with the paste - each job gets a 'Find on LinkedIn' search link instead."}
        </div>
      )}
      <button type="button" onClick={read} disabled={!text.trim() || busy}>Read jobs from the pasted text</button>
      {rows.length === 0 && text.trim() !== "" && summary === null && <span className="muted"> Press the button to see the jobs found in the text.</span>}
      {rows.length > 0 && (
        <>
          <p><b>{rows.length}</b> job(s) found, {withLink} with an exact link{rows.some((r) => r.tooOld) ? `; jobs older than ${maxAge} days are unticked` : ""}. Untick any you do not want, then import.</p>
          <DataTable rows={rows} rowKey={(r, i) => `${i}-${r.title}-${r.company}`} columns={[
            { header: "Import", render: (r) => <input type="checkbox" aria-label={`Import ${r.title} at ${r.company}`} checked={r.pick} disabled={Boolean(r.result)} onChange={(e) => set(rows.indexOf(r), { pick: e.target.checked })} /> },
            { header: "Title", render: (r) => <>{r.title}{r.description ? <span className="muted"> (with description)</span> : null}</> },
            { header: "Company", render: (r) => r.company },
            { header: "Location", render: (r) => [r.location, r.workMode].filter(Boolean).join(" · ") || "—" },
            { header: "Posted", render: (r) => (r.posted ?? "—") + (r.tooOld ? " (too old)" : "") },
            { header: "Link", render: (r) => <JobLinkCell url={linkedinJobUrl(r.jobId)} title={r.title} company={r.company} /> },
            { header: "On LinkedIn", render: (r) => (r.applied ? "Applied" : "—") },
            { header: "Result", render: (r) => r.result ?? "" },
          ]} />
          <button type="button" className="primary" onClick={() => void importAll()} disabled={busy || picked.length === 0}>{busy ? "Importing…" : `Import ${picked.length} selected job(s)`}</button>
        </>
      )}
      {summary && <Alert kind="success">{summary}</Alert>}
    </details>
  );
}
