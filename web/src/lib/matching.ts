// Pure helpers for the Job Matching page. The score and eligibility are computed by the backend; this only formats them. No React: unit-tested.
export const ELIG_ICON: Record<string, string> = { PASS: "✅", FAIL: "❌", UNKNOWN: "❓", NOT_APPLICABLE: "➖" };
/** UNKNOWN is never shown as PASS (or 0): an unrecognised status shows a neutral dot, never a tick. */
export const eligLabel = (status: string): string => `${ELIG_ICON[status] ?? "•"} ${status}`;

export const CATEGORY_LABELS: Record<string, string> = {
  technical_skills: "Technical skills", experience: "Relevant experience", projects: "Projects", responsibilities: "Responsibilities", education: "Education", location: "Location & work mode", seniority: "Seniority",
};
export const categoryLabel = (name: string): string => CATEGORY_LABELS[name] ?? name;

export const RECOMMENDATIONS = ["RECOMMENDED", "BELOW_THRESHOLD", "NOT_ELIGIBLE", "ELIGIBILITY_UNRESOLVED", "INSUFFICIENT_DATA"];
export const ELIGIBILITY_FILTERS = ["PASS", "FAIL", "UNKNOWN"];
export const HISTORY_PAGE_SIZE = 15;
export const MAX_JD_CHARS = 50000;

/** Share of the 18 key profile fields that are filled in. */
export const completeness = (missingCount: number): number => Math.max(0, 1 - missingCount / 18);
export const pct = (ratio: number): string => `${Math.round(ratio * 100)}%`;
/** 62.7 -> "62.7", 80 -> "80" (Python's :g) */
export const g = (n: number): string => String(Number(n.toPrecision(6)));

export type AlertKind = "success" | "error" | "warning";
export const recommendationKind = (code: string): AlertKind => (code === "RECOMMENDED" ? "success" : code === "NOT_ELIGIBLE" ? "error" : "warning");

export interface HistoryFilters {
  minScore: number; maxScore: number; eligibility: string; recommendation: string; title: string; company: string; dateFrom: string; dateTo: string;
  maxExperience: number;            // jobs asking for at most this many years (0 = no filter; jobs that state no minimum always match)
  latestOnly: boolean;              // one row per job: its newest match
  preferredOnly: boolean;           // only jobs that fit the saved preferences (titles, locations) or that you imported yourself
  applied: "any" | "no" | "yes";  // no = not applied yet (what is left to do); yes = you applied
  sort: "score" | "date";
}
export const NO_FILTERS: HistoryFilters = {
  minScore: 0, maxScore: 100, eligibility: "Any", recommendation: "Any", title: "", company: "", dateFrom: "", dateTo: "", maxExperience: 0, latestOnly: true, preferredOnly: true, applied: "any", sort: "score",
};
export const APPLIED_LABELS: Record<HistoryFilters["applied"], string> = { any: "Any", no: "Not applied yet", yes: "Applied" };
/** States in which an application has been (or may have been) sent - the switch shows "applied" and cannot be turned off. */
export const APPLIED_STATES = ["SUBMITTING", "SUBMITTED", "VERIFICATION_PENDING", "VERIFIED"];
export const isApplied = (state: string | null | undefined): boolean => Boolean(state && APPLIED_STATES.includes(state));
export const SORT_LABELS: Record<HistoryFilters["sort"], string> = { score: "Highest score first", date: "Newest first" };

/** "71.3%" - the match score as a percentage (the engine's 0-100 score). */
export const scoreLabel = (score: number): string => `${g(score)}%`;
/** Years of experience the job asks for: 5 -> "5+ yrs", null -> "-" (the job states none). */
export const expLabel = (years: number | null | undefined): string => (years === null || years === undefined ? "-" : `${g(years)}+ yrs`);
export const ROLE_FIT_LABELS: Record<string, string> = { same: "✅ same function", adjacent: "↔️ adjacent function", different: "⚠️ different function", unknown: "-" };
export const roleFitLabel = (relation: string | null | undefined): string => ROLE_FIT_LABELS[relation ?? "unknown"] ?? "-";
/** A score typed into the range boxes: whole number kept within 0-100 (an empty box is 0). Nothing else is rewritten while the user types. */
export const clampScore = (n: number): number => Math.min(100, Math.max(0, Math.round(n) || 0));
/** Explains an empty result caused by Min above Max (the boxes are never silently changed). */
export const rangeProblem = (minScore: number, maxScore: number): string | null => (minScore > maxScore ? `Min score (${minScore}) is higher than Max score (${maxScore}), so no job can match.` : null);

/** Query for GET /api/matching/results. One extra row is requested to learn whether a next page exists. */
export function historyParams(f: HistoryFilters, page: number, size = HISTORY_PAGE_SIZE): Record<string, string | number> {
  const p: Record<string, string | number> = { limit: size + 1, skip: (Math.max(1, page) - 1) * size };
  if (f.recommendation !== "Any") p.recommendation_code = f.recommendation;
  if (f.minScore) p.min_score = f.minScore;
  if (f.maxScore < 100) p.max_score = f.maxScore;
  if (f.maxExperience > 0) p.max_experience_required = f.maxExperience;
  if (f.latestOnly) p.latest_only = "true";
  if (f.preferredOnly) p.preferred_only = "true";
  if (f.applied !== "any") p.applied = f.applied;
  p.sort = f.sort;
  if (f.eligibility !== "Any") p.eligibility = f.eligibility;
  if (f.title) p.job_title = f.title;
  if (f.company) p.company = f.company;
  if (f.dateFrom) p.date_from = `${f.dateFrom}T00:00:00`;
  if (f.dateTo) p.date_to = `${f.dateTo}T23:59:59`;
  return p;
}

/** The request body for POST /api/matching/score. Claude is only requested when the backend says it is available. */
export function scoreBody(job: Record<string, string>, allowUnreviewed: boolean, useAi: boolean, aiEnabled: boolean) {
  return { job, allow_unreviewed: allowUnreviewed, use_ai: aiEnabled ? useAi : false };
}

/** Decode an uploaded job-description file as UTF-8 (BOM stripped, capped). Throws if it is not UTF-8 text. */
export function decodeJobDescription(bytes: ArrayBuffer): string {
  const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes).replace(/^﻿/, "");
  return text.slice(0, MAX_JD_CHARS);
}

export function categoryProgress(cat: any): { ratio: number; text: string } {
  if (cat.status === "EVALUATED") return { ratio: cat.ratio, text: `${Number(cat.earned_points).toFixed(1)} / ${g(cat.weight)} points` };
  return { ratio: 0, text: `UNKNOWN - not scored (weight ${g(cat.weight)}). ${cat.notes?.[0] ?? ""}`.trim() };
}
