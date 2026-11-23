// LinkedIn helpers. IMPORTANT: this module never contacts LinkedIn. It only (a) BUILDS a search link that the USER opens in their own browser and
// (b) READS the job id out of a link the user pasted. LinkedIn is manual-only in this project (no scraping, no automated login); see config/platform_policies.yaml.

export interface LinkedInSearch {
  keywords: string;
  location?: string;
  workModes?: Array<"onsite" | "remote" | "hybrid">;
  postedWithin?: "any" | "day" | "week" | "month";
  sortRecent?: boolean;
}

const WORK_MODE_CODE: Record<string, string> = { onsite: "1", remote: "2", hybrid: "3" };       // LinkedIn's f_WT filter values
const POSTED_CODE: Record<string, string> = { day: "r86400", week: "r604800", month: "r2592000" };  // LinkedIn's f_TPR filter values

/** A LinkedIn job-search URL for the user to open themselves. Returns null when there is nothing to search for. */
export function linkedinSearchUrl(s: LinkedInSearch): string | null {
  const keywords = s.keywords.trim().replace(/\s+/g, " ");
  if (!keywords) return null;
  const q = new URLSearchParams({ keywords: keywords.slice(0, 120) });
  const location = (s.location ?? "").trim();
  if (location) q.set("location", location.slice(0, 100));
  const wt = (s.workModes ?? []).map((m) => WORK_MODE_CODE[m]).filter(Boolean);
  if (wt.length) q.set("f_WT", [...new Set(wt)].join(","));
  const tpr = POSTED_CODE[s.postedWithin ?? "any"];
  if (tpr) q.set("f_TPR", tpr);
  if (s.sortRecent) q.set("sortBy", "DD");
  return `https://www.linkedin.com/jobs/search/?${q.toString()}`;
}

export interface LinkedInJobRef { jobId: string; canonicalUrl: string }

const HOST_OK = /^(?:[a-z]{2,3}\.|www\.)?linkedin\.com$/i;

/**
 * The numeric job id from a LinkedIn job link the user pasted. Accepts /jobs/view/<id>, /jobs/view/<slug>-<id>, and any LinkedIn URL carrying ?currentJobId=<id>.
 * Only real linkedin.com hosts (not look-alikes such as linkedin.com.evil.example). Returns null for anything else.
 */
export function parseLinkedInJobUrl(input: string): LinkedInJobRef | null {
  let u: URL;
  try { u = new URL(input.trim()); } catch { return null; }
  if (u.protocol !== "https:" && u.protocol !== "http:") return null;
  if (u.username || u.password || !HOST_OK.test(u.hostname)) return null;
  const fromQuery = u.searchParams.get("currentJobId");
  const fromPath = /\/jobs\/view\/(?:[^/?#]*?-)?(\d{6,15})(?:[/?#]|$)/i.exec(u.pathname + "/")?.[1];
  const id = [fromQuery, fromPath].find((x) => x && /^\d{6,15}$/.test(x));
  return id ? { jobId: id, canonicalUrl: `https://www.linkedin.com/jobs/view/${id}/` } : null;
}

/** A SUGGESTION for the title from pasted page text: the first non-empty line, only when it is short and looks like a title. The user can edit it. */
export function suggestTitle(pasted: string): string {
  const first = pasted.split(/\r?\n/).map((l) => l.trim()).find(Boolean) ?? "";
  return first.length >= 3 && first.length <= 120 && !/[.!?]$/.test(first) ? first : "";
}
