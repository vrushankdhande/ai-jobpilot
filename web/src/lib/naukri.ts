// Naukri helpers. IMPORTANT: this module never contacts Naukri. It only (a) BUILDS a search link that the USER opens in their own browser and
// (b) READS the job id out of a link the user pasted. Naukri is manual-only in this project (no scraping, no automated login); see config/platform_policies.yaml.
// The search-link parameters (k, l, experience, jobAge) follow Naukri's public URLs as commonly seen; Naukri may change them, and this app cannot check that
// without contacting the site. If a filter is ignored, set it on Naukri's own page.

export interface NaukriSearch {
  keywords: string;
  location?: string;
  experienceYears?: number | null;
  postedWithin?: "any" | "day" | "3days" | "week" | "15days" | "month";
}

const JOB_AGE: Record<string, string> = { day: "1", "3days": "3", week: "7", "15days": "15", month: "30" };   // Naukri's jobAge filter (days)

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

/** A Naukri job-search URL for the user to open themselves. Returns null when there is nothing to search for. */
export function naukriSearchUrl(s: NaukriSearch): string | null {
  const keywords = s.keywords.trim().replace(/\s+/g, " ").slice(0, 120);
  const kwSlug = slug(keywords);
  if (!keywords || !kwSlug) return null;
  const location = (s.location ?? "").trim().replace(/\s+/g, " ").slice(0, 100);
  const locSlug = slug(location);
  const q = new URLSearchParams({ k: keywords });
  if (location) q.set("l", location);
  if (s.experienceYears != null && Number.isFinite(s.experienceYears) && s.experienceYears >= 0 && s.experienceYears <= 40) q.set("experience", String(Math.floor(s.experienceYears)));
  const age = JOB_AGE[s.postedWithin ?? "any"];
  if (age) q.set("jobAge", age);
  const path = locSlug ? `${kwSlug}-jobs-in-${locSlug}` : `${kwSlug}-jobs`;
  return `https://www.naukri.com/${path}?${q.toString()}`;
}

export interface NaukriJobRef { jobId: string; canonicalUrl: string }

const HOST_OK = /^(?:www\.|m\.)?naukri\.com$/i;

/**
 * The numeric job id from a Naukri job link the user pasted, e.g. https://www.naukri.com/job-listings-ai-engineer-acme-pune-2-to-5-years-170924012345?src=...
 * Only real naukri.com hosts (not look-alikes such as naukri.com.evil.example). The stored link drops the query string (tracking parameters).
 * Returns null for anything else.
 */
export function parseNaukriJobUrl(input: string): NaukriJobRef | null {
  let u: URL;
  try { u = new URL(input.trim()); } catch { return null; }
  if (u.protocol !== "https:" && u.protocol !== "http:") return null;
  if (u.username || u.password || !HOST_OK.test(u.hostname)) return null;
  const m = /^\/job-listings-[a-z0-9-]*?-(\d{9,15})\/?$/i.exec(u.pathname);
  if (!m) return null;
  return { jobId: m[1], canonicalUrl: `https://www.naukri.com${u.pathname.replace(/\/$/, "")}` };
}

// ---- bulk paste: a Naukri results page that the USER copied (Ctrl+A, Ctrl+C). Nothing is fetched; this only splits text. ----
export interface PastedNaukriJob {
  title: string; company: string; experience: string | null; salary: string | null; location: string | null
  posted: string | null; postedDate: string | null; text: string
}

const EXP = /^(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*Yrs?$|^(\d{1,2})\+?\s*Yrs?$|^Fresher$/i;
const POSTED = /^(?:just now|today|few hours ago|\d{1,2}\s*hours?\s*ago|\d{1,2}\+?\s*days?\s*ago|yesterday)$/i;
const SALARY = /^(?:not disclosed|₹|rs\.?\s|\d[\d.,\s]*(?:-|–)\s*\d[\d.,\s]*\s*(?:lacs?|lakhs?|lpa|cr)\b|.*\b(?:lacs?|lakhs?)\s*p\.?a\.?$)/i;
const NOISE = /^(?:save|saved|hide|apply|easy apply|send me jobs like this|\d(?:\.\d)?|\d[\d,.]*k?\s*reviews?|promoted|sponsored|new|\|)$/i;

/** "1 Day Ago" -> ISO date (YYYY-MM-DD) counted back from `today`; anything vaguer -> null. */
export function postedToDate(posted: string | null, today: Date = new Date()): string | null {
  if (!posted) return null;
  const p = posted.toLowerCase();
  let days: number | null = null;
  if (/just now|today|hours? ago/.test(p)) days = 0;
  else if (p === "yesterday") days = 1;
  else { const m = p.match(/^(\d{1,2})\s*days?\s*ago$/); if (m) days = Number(m[1]); }       // "30+ Days Ago" is open-ended: left unknown
  if (days === null) return null;
  const d = new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()) - days * 86400000);
  return d.toISOString().slice(0, 10);
}

/** Split the text of a copied Naukri results page into one entry per job card. A card is anchored on its experience line ("5-10 Yrs");
 *  the two meaningful lines above it are the title and company, the lines after it are salary, location, snippet and skills.
 *  Unknown parts stay null - nothing is guessed. Cards without a title or company are dropped. */
export function parseNaukriResults(text: string, today: Date = new Date()): PastedNaukriJob[] {
  const lines = text.replace(/\r/g, "").split("\n").map((l) => l.replace(/\s+/g, " ").trim()).filter(Boolean);
  const anchors = lines.map((l, i) => (EXP.test(l) ? i : -1)).filter((i) => i >= 0);
  const heads = anchors.map((a) => {
    const picked: number[] = [];
    for (let i = a - 1; i >= 0 && picked.length < 2; i--) {
      if (NOISE.test(lines[i]) || POSTED.test(lines[i])) continue;
      if (anchors.includes(i)) break;
      picked.push(i);
    }
    return picked.length === 2 ? { title: picked[1], company: picked[0] } : null;
  });
  const out: PastedNaukriJob[] = [];
  anchors.forEach((a, k) => {
    const h = heads[k];
    if (!h) return;
    const nextStart = heads.slice(k + 1).find(Boolean)?.title ?? lines.length;
    const tail = lines.slice(a + 1, nextStart);
    const postedIdx = tail.findIndex((l) => POSTED.test(l));
    const body = (postedIdx >= 0 ? tail.slice(0, postedIdx) : tail).filter((l) => !NOISE.test(l));
    let salary: string | null = null; let location: string | null = null;
    if (body[0] && SALARY.test(body[0])) salary = body.shift()!;
    if (body[0] && body[0].length <= 80 && !/[.!?]$/.test(body[0])) location = body.shift()!;
    const posted = postedIdx >= 0 ? tail[postedIdx] : null;
    const title = lines[h.title]; const company = lines[h.company]; const experience = lines[a];
    out.push({
      title, company, experience, salary, location, posted, postedDate: postedToDate(posted, today),
      text: [title, company, `Experience: ${experience}`, location ? `Location: ${location}` : "", salary ? `Salary: ${salary}` : "", ...body].filter(Boolean).join("\n"),
    });
  });
  return out;
}
