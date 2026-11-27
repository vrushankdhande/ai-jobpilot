import { useEffect, useMemo, useState, type ClipboardEvent, type ReactNode } from "react";
import { api } from "../api";
import { useFetch } from "../hooks";
import { fmtDt } from "../format";
import { buildImportBody, importMessage } from "../lib/discovery";
import { linkedinSearchUrl } from "../lib/linkedin";
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
    <details>
      <summary>1. 🔗 Open the LinkedIn search</summary>
      <p className="muted">Opens LinkedIn in a new tab with your search filled in. Then copy the results page into box 2.</p>
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

interface LinkedInRow {
  job_id: string; job_title: string | null; company_name: string | null; location: string | null; experience_min: number | null; experience_max: number | null
  posted_date: string | null; discovered_at: string; match_score: number | null; eligibility: string | null; job_url: string | null; application_status: string
}
const expText = (r: LinkedInRow) => (r.experience_min == null ? "Not stated" : r.experience_max != null && r.experience_max !== r.experience_min ? `${r.experience_min}-${r.experience_max} yrs` : `${r.experience_min}+ yrs`);

/** The LinkedIn jobs you imported, with their latest match score, eligibility and application status. Reads only this app's own database. */
export function LinkedInJobsTable({ reloadKey = 0 }: { reloadKey?: number }) {
  const t = useFetch<{ total: number; items: LinkedInRow[] }>("/api/jobs/linkedin/table", { k: reloadKey });
  return (
    <details>
      <summary>Already imported from LinkedIn{t.data ? ` (${t.data.total ?? t.data.items.length})` : ""}</summary>
      {t.loading && !t.data && <Loading />}
      <ErrorBox result={t.result} />
      {t.data && t.data.items.length === 0 && <Empty message="No LinkedIn jobs yet." hint="Open a LinkedIn search (box 1), copy the results page and paste it into box 2. Imported jobs also appear on the Job Matching page." />}
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
export function LinkedInBulkPaste({ onDone, children }: { onDone: () => void; children?: ReactNode }) {
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
  // the jobs are read as soon as something is pasted (or the page address / age limit changes) - no extra button
  useEffect(() => {
    setSummary(null);
    if (!text.trim()) { setRows([]); return; }
    const jobs = attachLinks(parseLinkedInResults(text), links, address);
    setRows(jobs.map((j) => {
      const tooOld = maxAge !== null && j.ageDays !== null && j.ageDays > maxAge;
      return { ...j, tooOld, pick: !tooOld };
    }));
  }, [text, links, address, maxAge]);
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
      if (!res.data.match && !r.description) note += " Not scored yet (no description in the list): open it and paste its description under 'Import a job manually' to score it.";
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
    <details>
      <summary>2. 📋 Paste the LinkedIn results here</summary>
      <p className="muted">On the LinkedIn results page press <b>Ctrl+A</b>, then <b>Ctrl+C</b>, and paste here (<b>Ctrl+V</b>). The jobs and their links are read automatically - then press Import.
        JobPilot never opens LinkedIn itself; your messages on the page are ignored and the pasted text is not stored.</p>
      <Field label="Paste the copied LinkedIn search page"><textarea style={{ minHeight: 140 }} value={text} onPaste={onPaste} onChange={(e) => setText(e.target.value)} /></Field>
      {text.trim() !== "" && (
        <div className="muted" role="status">
          {links.length ? `🔗 ${new Set(links.map((l) => l.id)).size} job link(s) found in what you copied.` : "No job links came with the paste - each job gets a 'Find on LinkedIn' search link instead."}
        </div>
      )}
      {rows.length === 0 && text.trim() !== "" && <Alert kind="warning">No jobs found in what you pasted. Copy the whole LinkedIn <b>search results</b> page (Ctrl+A, Ctrl+C) and paste again.</Alert>}
      {rows.length > 0 && rows.some((r) => !r.jobId) && (
        <Field label="Page address (optional - gives the job you had open its exact link)"><input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="https://www.linkedin.com/jobs/search/?currentJobId=…" /></Field>
      )}
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
      {children}
    </details>
  );
}
