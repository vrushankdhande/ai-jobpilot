// Reads a LinkedIn job-search page that the USER copied themselves (Ctrl+A, Ctrl+C) and pasted into JobPilot.
// IMPORTANT: nothing here contacts LinkedIn. It only splits text (and the clipboard's HTML copy, for the job links) that the user handed over.
// A copied page also contains the user's messages, notifications and other people's names: only the jobs list and the open job's
// "About the job" text are read; everything else is ignored and the pasted text is never stored or sent anywhere.
import { parseLinkedInJobUrl } from "./linkedin";

export interface PastedLinkedInJob {
  title: string;
  company: string;
  location: string | null;
  workMode: "remote" | "hybrid" | "onsite" | null;
  posted: string | null;        // as LinkedIn shows it, e.g. "2 weeks ago"
  postedDate: string | null;    // YYYY-MM-DD, approximate (LinkedIn only gives "N units ago")
  ageDays: number | null;
  salary: string | null;
  applied: boolean;             // LinkedIn shows "Applied" on this card
  selected: boolean;            // the job that was open on the right of the page
  jobId: string | null;         // LinkedIn job number, when the copied HTML (or the page address) contained it
  description: string | null;   // only for the open job: the "About the job" text
}

export interface JobLink { id: string; text: string }

const START = [/^How AI search works$/i, /^How promoted jobs are ranked$/i, /^Jobs you may be interested in$/i, /^[\d,]+\+? results?$/i];
const END = [/^Are these results helpful\??$/i, /^Your feedback helps us improve/i, /^(Previous|Next)$/i, /^About$/i, /^Accessibility$/i];
const META = [
  /^·$/, /^(Re)?posted\b/i, /^Applied$/i, /^Viewed$/i, /^Saved$/i, /^Promoted\b/i, /^Easy Apply$/i, /^Be an early applicant$/i,
  /^Actively (reviewing|recruiting)\b/i, /^Be among the first/i, /\bworks? here$/i, /\balumni\b/i, /^\d+\s+connections?\b/i, /^Your profile matches/i,
  /^Top applicant$/i, /^Fast growing\b/i, /^\d+\s+applicants?$/i, /^Response insights/i, /^Early applicant$/i, /^Hiring\b/i, /^No longer accepting/i,
  /^[$₹€£]\s?\d/, /^\d[\d,.]*\s?(K|L|LPA|lakh)\b.*\/(yr|mo|hr)/i, /^(Medical|Vision|Dental|401\(k\))(,|$)/i, /^\d+ benefits?$/i,
];
const isMeta = (l: string) => META.some((r) => r.test(l));

/** "AI EngineerAI Engineer" (LinkedIn repeats a title for screen readers) -> "AI Engineer". */
export function undouble(s: string): string {
  const t = s.trim();
  for (let i = 1; i < t.length; i++) {
    const a = t.slice(0, i).trim();
    if (a && a.length * 2 >= t.length - 2 && a === t.slice(i).trim()) return a;
  }
  return t;
}

function cleanTitle(raw: string): { title: string; selected: boolean } {
  let t = raw.trim();
  const selected = /^Selected,\s*/i.test(t);
  t = t.replace(/^Selected,\s*/i, "").replace(/\s*\((Verified job|with verification)\)\s*/gi, "").replace(/\s+with verification\s*$/i, "");
  return { title: undouble(t).replace(/\s+/g, " ").trim(), selected };
}

const UNIT_DAYS: Record<string, number> = { minute: 0, hour: 0, day: 1, week: 7, month: 30, year: 365 };

export function ageInDays(posted: string | null): number | null {
  const m = (posted ?? "").toLowerCase().match(/(\d+)\s*(minute|hour|day|week|month|year)s?\s+ago/);
  return m ? Number(m[1]) * UNIT_DAYS[m[2]] : null;
}

function dateFromAge(days: number | null, today: Date): string | null {
  if (days === null) return null;
  return new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()) - days * 86400000).toISOString().slice(0, 10);
}

function place(raw: string): { location: string | null; workMode: PastedLinkedInJob["workMode"] } {
  const mode = raw.match(/\((Remote|Hybrid|On-site|Onsite)\)/i)?.[1]?.toLowerCase().replace("-", "") ?? null;
  const location = raw.replace(/\((Remote|Hybrid|On-site|Onsite)\)/i, "").replace(/\s+(Division|District|Metropolitan Region|Area)\b/gi, "").replace(/\s+/g, " ").trim();
  return { location: location || null, workMode: (mode as PastedLinkedInJob["workMode"]) ?? null };
}

/** The open job's "About the job" text, cut before LinkedIn's own sections (benefits, applicant insights, company, messages). */
function aboutTheJob(lines: string[]): string | null {
  const start = lines.findIndex((l) => /^About the job$/i.test(l.trim()));
  if (start < 0) return null;
  const out: string[] = [];
  for (const raw of lines.slice(start + 1)) {
    const l = raw.trim();
    if (/^(Show more|Show less|Benefits found in job post|See how you compare|About the company|Set alert for similar jobs|Requirements added by the job poster|People you can reach out to|Meet the hiring team)$/i.test(l)) break;
    const more = l.match(/^(.*?)(…|\.\.\.)\s*more$/i);
    if (more) { if (more[1].trim()) out.push(more[1].trim()); break; }
    out.push(raw.trimEnd());
  }
  const text = out.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  return text.length >= 50 ? text : null;
}

/** Split a copied LinkedIn search page into jobs. Unknown values stay null; nothing is guessed. */
export function parseLinkedInResults(text: string, today: Date = new Date()): PastedLinkedInJob[] {
  const all = text.replace(/\r/g, "").split("\n");
  const lines = all.map((l) => l.trim()).filter(Boolean);
  let from = -1;
  lines.forEach((l, i) => { if (START.some((r) => r.test(l)) && (from < 0 || i - from < 8)) from = i; });   // the last header line before the cards
  const end = lines.findIndex((l, i) => i > from && END.some((r) => r.test(l)));
  const region = lines.slice(from + 1, end < 0 ? undefined : end);

  const jobs: PastedLinkedInJob[] = [];
  let i = 0;
  while (i < region.length) {
    if (isMeta(region[i])) { i++; continue; }
    const { title, selected } = cleanTitle(region[i]);
    const company = region[i + 1] && !isMeta(region[i + 1]) ? region[i + 1].trim() : null;
    const locRaw = region[i + 2] && !isMeta(region[i + 2]) ? region[i + 2] : null;
    if (!title || !company) { i++; continue; }
    i += locRaw ? 3 : 2;
    let posted: string | null = null, salary: string | null = null, applied = false, marked = false;
    while (i < region.length && isMeta(region[i])) {
      marked = true;
      const l = region[i];
      const p = l.match(/^(?:Re)?posted\s+(.*)$/i);
      if (p) posted = undouble(p[1]);
      else if (/^Applied$/i.test(l)) applied = true;
      else if (/^[$₹€£]\s?\d/.test(l) || /\/(yr|mo|hr)\b/i.test(l)) salary = undouble(l.replace(/\s-\s/, " - "));
      i++;
    }
    const ageDays = ageInDays(posted);
    if (from < 0 && !(locRaw && marked)) continue;          // no results header: keep only blocks that look like LinkedIn job cards
    jobs.push({ title, company, ...place(locRaw ?? ""), posted, postedDate: dateFromAge(ageDays, today), ageDays, salary, applied, selected,
                jobId: null, description: null });
  }
  const open = jobs.find((j) => j.selected);
  if (open) open.description = aboutTheJob(all);
  return jobs;
}

/** Job links from the HTML copy the browser puts on the clipboard next to the text: every link to /jobs/view/<number>. */
export function jobLinksFromHtml(html: string): JobLink[] {
  if (!html.trim()) return [];
  const doc = new DOMParser().parseFromString(html, "text/html");
  const out: JobLink[] = [];
  doc.querySelectorAll("a[href]").forEach((a) => {
    const href = a.getAttribute("href") ?? "";
    const abs = /^https?:/i.test(href) ? href : `https://www.linkedin.com${href.startsWith("/") ? "" : "/"}${href}`;
    const ref = parseLinkedInJobUrl(abs);
    if (ref) out.push({ id: ref.jobId, text: (a.textContent ?? "").replace(/\s+/g, " ").trim() });
  });
  return out;
}

const norm = (s: string) => cleanTitle(s).title.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

/** Give each job its LinkedIn job number: by the link's own text (the job title), else by order when the counts agree, else the page address for the open job. */
export function attachLinks(jobs: PastedLinkedInJob[], links: JobLink[], pageAddress = ""): PastedLinkedInJob[] {
  const unique: JobLink[] = [];
  for (const l of links) if (!unique.some((u) => u.id === l.id)) unique.push(l); else if (l.text) { const u = unique.find((x) => x.id === l.id)!; u.text ||= l.text; }
  const used = new Set<string>();
  const out = jobs.map((j) => ({ ...j }));
  for (const j of out) {
    const t = norm(j.title);
    const hit = unique.find((l) => !used.has(l.id) && l.text && (norm(l.text) === t || norm(l.text).startsWith(t) || (t.length > 8 && norm(l.text).includes(t))));
    if (hit) { j.jobId = hit.id; used.add(hit.id); }
  }
  if (!out.some((j) => j.jobId) && unique.length === out.length) out.forEach((j, k) => { j.jobId = unique[k].id; });
  const page = parseLinkedInJobUrl(pageAddress);
  const open = out.find((j) => j.selected);
  if (page && open && !open.jobId && !out.some((j) => j.jobId === page.jobId)) open.jobId = page.jobId;
  return out;
}

export const linkedinJobUrl = (id: string | null): string | null => (id ? `https://www.linkedin.com/jobs/view/${id}/` : null);
