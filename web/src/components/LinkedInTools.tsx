import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api } from "../api";
import { useFetch } from "../hooks";
import { fmtDt } from "../format";
import { buildImportBody, importMessage } from "../lib/discovery";
import { linkedinSearchUrl, parseLinkedInJobUrl, suggestTitle } from "../lib/linkedin";
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
          { header: "Link", render: (r) => (r.job_url ? <a href={r.job_url} target="_blank" rel="noopener noreferrer">Open on LinkedIn ↗</a> : "—") },
          { header: "Application", render: (r) => r.application_status.replaceAll("_", " ").toLowerCase() },
        ]} />
      )}
    </details>
  );
}
