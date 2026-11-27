import { useEffect, useState } from "react";
import { jobSiteLinks } from "../lib/jobLinks";
import { DataTable, Field } from "./ui";

/** One-click links to each job site's own "recommended for you" page and a ready-made search. You open them, signed in to your account;
 *  JobPilot never opens or reads them. Copy a results page back into the paste boxes below to import the jobs. */
export function JobSiteLinks({ defaultTitle, defaultCity }: { defaultTitle?: string | null; defaultCity?: string | null }) {
  const [title, setTitle] = useState("AI Engineer");
  const [city, setCity] = useState("Pune");
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (touched) return;
    if (defaultCity) setCity(defaultCity.split(",")[0].trim());
  }, [defaultTitle, defaultCity, touched]);
  const rows = jobSiteLinks(title, city);
  const open = (href: string | null, text: string) => (href ? <a href={href} target="_blank" rel="noopener noreferrer">{text} ↗</a> : <span className="muted">—</span>);
  return (
    <details className="fold">
      <summary><h2>Job recommendations on job sites</h2> <span className="muted">· optional: browse the sites yourself (alert e-mails already bring their jobs in)</span></summary>
      <div className="grid cols-2">
        <Field label="Job title for the searches"><input value={title} onChange={(e) => { setTouched(true); setTitle(e.target.value); }} /></Field>
        <Field label="City"><input value={city} onChange={(e) => { setTouched(true); setCity(e.target.value); }} /></Field>
      </div>
      <DataTable rows={rows} rowKey={(r) => r.site} columns={[
        { header: "Site", render: (r) => <b>{r.site}</b> },
        { header: "Recommended for you", render: (r) => <>{open(r.recommended, "Open recommendations")}{r.note ? <span className="muted"> ({r.note})</span> : null}</> },
        { header: `Search: ${title || "…"} in ${city || "…"}, last 7 days`, render: (r) => open(r.search, "Open search") },
      ]} />
      <p className="muted">To bring jobs into JobPilot: on the results page press Ctrl+A, Ctrl+C and paste into the LinkedIn or Naukri box below; single
        Indeed or Instahyre jobs go in the manual import at the bottom of this page. JobPilot never opens these sites itself.</p>
    </details>
  );
}
