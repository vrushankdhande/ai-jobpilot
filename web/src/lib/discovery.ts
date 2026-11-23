// Pure logic for the Job Discovery page (port of the helpers in frontend/pages/02_job_discovery.py). No React; unit-tested.
export const STATUS_ICON: Record<string, string> = { ENABLED: "✅", MANUAL_ONLY: "✋", DISABLED: "⛔", PENDING_POLICY_REVIEW: "⏳" };
export const SOURCE_LABEL: Record<string, string> = { career_pages: "Company career pages", linkedin: "LinkedIn", indeed: "Indeed", naukri: "Naukri", instahyre: "Instahyre", manual: "Manual entry" };
export const sourceLabel = (s: string): string => SOURCE_LABEL[s] ?? s;

export const WORK_MODES = ["remote", "hybrid", "onsite"];
export const EMPLOYMENT_TYPES = ["full-time", "part-time", "contract", "internship", "freelance"];
export const SORTS: Record<string, string> = {
  "Date discovered": "discovered_at", "Date posted": "posted_date", Company: "company_name", Title: "job_title", Experience: "experience_min", "Salary (max)": "salary_max",
};

/** Split on newlines and commas, trim, drop blanks (same as the Streamlit page). */
export function splitList(text: string): string[] {
  return text.replaceAll(",", "\n").split("\n").map((x) => x.trim()).filter(Boolean);
}

export interface DiscoverForm {
  usePrefs: boolean; titles: string; skills: string; locations: string; companies: string; modes: string[]; employment: string[];
  minSalary: number; expMax: number; postedDays: number; sources: string[];
}
export function buildDiscoverBody(f: DiscoverForm) {
  return {
    sources: f.sources, use_preferences: f.usePrefs, titles: splitList(f.titles), skills: splitList(f.skills), locations: splitList(f.locations), companies: splitList(f.companies),
    work_modes: f.modes, employment_types: f.employment, min_salary: f.minSalary || null, experience_max: f.expMax || null, posted_within_days: f.postedDays || null,
  };
}

/** Local midnight ISO (no zone), as the Streamlit page sent it. */
export const dayStartIso = (ymd: string): string => `${ymd}T00:00:00`;
export function nextDayIso(ymd: string): string {
  const [y, m, d] = ymd.split("-").map(Number);
  const n = new Date(Date.UTC(y, m - 1, d + 1));
  const p = (v: number) => String(v).padStart(2, "0");
  return `${n.getUTCFullYear()}-${p(n.getUTCMonth() + 1)}-${p(n.getUTCDate())}T00:00:00`;
}

export interface JobFilters {
  title: string; company: string; location: string; source: string; maxExp: number; minScore: number; sortLabel: string; desc: boolean; from: string; to: string; reviewOnly: boolean; preferredOnly: boolean;
}
export const defaultFilters = (): JobFilters => ({ title: "", company: "", location: "", source: "All", maxExp: 0, minScore: 0, sortLabel: "Date discovered", desc: true, from: "", to: "", reviewOnly: false, preferredOnly: true });

export function buildJobParams(f: JobFilters): Record<string, string | number | boolean> {
  const p: Record<string, string | number | boolean> = { sort_by: SORTS[f.sortLabel] ?? "discovered_at", descending: f.desc };
  if (f.title) p.title = f.title;
  if (f.company) p.company = f.company;
  if (f.location) p.location = f.location;
  if (f.source !== "All") p.source = f.source;
  if (f.maxExp) p.max_experience = f.maxExp;
  if (f.minScore) p.min_match_score = f.minScore;
  if (f.preferredOnly) p.preferred_only = "true";
  if (f.from) p.discovered_after = dayStartIso(f.from);
  if (f.to) p.discovered_before = nextDayIso(f.to);
  if (f.reviewOnly) p.needs_review = true;
  return p;
}

const num = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 0 });
export function money(j: any): string {
  const lo = j.salary_min, hi = j.salary_max, cur = j.currency || "";
  if ((lo === null || lo === undefined) && (hi === null || hi === undefined)) return "—";
  return (lo && hi ? `${cur} ${num(lo)}–${num(hi)}` : `${cur} ${num(lo || hi)}`).trim();
}
const g = (v: number) => String(Number(v.toPrecision(12)));
export function exp(j: any): string {
  const lo = j.experience_min, hi = j.experience_max;
  const hasLo = lo !== null && lo !== undefined, hasHi = hi !== null && hi !== undefined;
  if (!hasLo && !hasHi) return "—";
  return hasLo && hasHi ? `${g(lo)}–${g(hi)} yrs` : `${g(hasLo ? lo : hi)}+ yrs`;
}
export const dup = (j: any): string => (j.needs_review ? "⚠️ possible duplicate" : j.dedup_status === "merged" ? "🔗 merged" : "unique");

export function sourceRows(sources: any[]) {
  return sources.map((s) => ({
    source: sourceLabel(s.source), status: `${STATUS_ICON[s.status] ?? "❔"} ${s.status}`, automated: s.automated_discovery ? "yes" : "no", manual: s.manual_import ? "yes" : "no",
    stored: s.jobs_stored ?? 0, why: s.reason || (s.source === "career_pages" ? `${s.companies_configured ?? 0} company(ies) configured in config/career_sources.yaml` : ""),
  }));
}
export const blockedSources = (sources: any[]) => sources.filter((s) => !s.automated_discovery && s.source !== "manual");

export function runSourceRows(run: any) {
  return Object.entries(run.sources ?? {}).map(([k, v]: [string, any]) => ({
    source: k, state: v.state, fetched: v.fetched, new: v.new, updated: v.updated, unchanged: v.unchanged, merged: v.merged, flagged: v.flagged, filtered: v.filtered_out,
    requests: v.requests, problems: (v.errors?.length ? v.errors : v.notes ?? []).join("; "),
  }));
}
export function runProgress(run: any): number {
  const done = Object.values(run.sources ?? {}).filter((s: any) => s.state !== "pending" && s.state !== "running").length;
  return Math.min(0.95, (done + 0.5) / Math.max(1, (run.requested_sources ?? [1]).length));
}

/** Only http(s) links are ever rendered as links (never javascript: or data:). */
export const httpUrl = (u: unknown): string | null => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);

export function jobRows(jobs: any[], scoreOf: Record<string, number>) {
  return jobs.map((j) => ({
    id: j.job_id, title: j.job_title || "(unknown)", company: j.company_name || "(unknown)", location: (j.location ?? []).join(", ") || "—", match: scoreOf[j.job_id] ?? null,
    mode: j.work_mode || "—", experience: exp(j), salary: money(j), posted: (j.posted_date || "").slice(0, 10) || "—", discovered: (j.discovered_at || "").slice(0, 10),
    sources: [...new Set((j.sources ?? []).map((s: any) => s.source))].sort().join(", "), status: j.status, duplicate: dup(j), url: httpUrl(j.job_url) ?? httpUrl((j.sources ?? []).find((s: any) => httpUrl(s.url))?.url),
  }));
}
export function bySource(jobs: any[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const j of jobs) out[j.source] = (out[j.source] ?? 0) + 1;
  return out;
}
export function latestScores(results: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (!Array.isArray(results)) return out;
  for (const r of results) if (!(r.job_id in out)) out[r.job_id] = r.match_score;          // newest first: keep the first seen
  return out;
}

export const IMPORT_MESSAGES: Record<string, string> = {
  insert: "Imported as a new job.", flag: "Imported, but it looks like a possible duplicate - see 'Needs review'.", merge: "This vacancy already existed: the source was added to it.",
  unchanged: "Already imported - nothing changed.", update: "Already imported - details updated.",
};
/** The import result, plus the score it was given straight away (when a reviewed profile exists). */
export const importMessage = (action: string, match?: { match_score: number; eligibility: string } | null): string =>
  (IMPORT_MESSAGES[action] ?? action) + (match ? ` Scored ${match.match_score}% · eligibility ${match.eligibility}.` : "");

/** Only non-empty fields are sent for a manual import. */
export function buildImportBody(url: string, job: Record<string, string>) {
  return { url: url || null, job: Object.fromEntries(Object.entries(job).filter(([, v]) => v)) };
}
