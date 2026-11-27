// Pure logic for the Application Queue page (unit-tested; no React). The backend enforces every rule - this only decides what to SHOW.
export const OVERVIEW_TABS = ["Needs your action", "Awaiting review / approval", "Queued & processing", "Manual handoff", "Failed / needs verification"] as const;
export type OverviewTab = (typeof OVERVIEW_TABS)[number];
export const TAB_STATES: Record<OverviewTab, string[] | null> = {
  // everything waiting for YOU, whatever the reason - the first tab, so an open item is never hidden behind another tab
  "Needs your action": ["READY_FOR_REVIEW", "AWAITING_USER_INPUT", "APPROVED", "MANUAL_HANDOFF_REQUIRED", "SUBMISSION_FAILED", "VERIFICATION_PENDING"],
  "Awaiting review / approval": ["READY_FOR_REVIEW", "AWAITING_USER_INPUT", "APPROVED"],
  "Queued & processing": null as any,
  "Manual handoff": ["MANUAL_HANDOFF_REQUIRED"],
  "Failed / needs verification": ["SUBMISSION_FAILED", "VERIFICATION_PENDING"],
};
/** Jobs whose title, company or label contain every word typed (any order, any case). An empty search returns the list unchanged. */
export function searchJobs<T extends { job_title?: string | null; company_name?: string | null }>(jobs: T[], query: string, labelOf: (j: T) => string = () => ""): T[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return jobs;
  return jobs.filter((j) => {
    const hay = `${j.job_title ?? ""} ${j.company_name ?? ""} ${labelOf(j)}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
}

/** What each tab is for, shown when it is empty - so an empty tab explains itself instead of a bare "nothing here". */
export const TAB_EMPTY: Record<OverviewTab, string> = {
  "Needs your action": "Nothing needs you right now. Prepared applications, missing answers, jobs to apply to yourself and failed or unclear submissions all show up here first.",
  "Awaiting review / approval": "No prepared application is waiting for your OK. After the morning job search, good matches (eligible and score at or above your rule) are prepared and appear here.",
  "Queued & processing": "Nothing is queued. The daily job search puts good matches in the queue; the background worker then prepares them (it never submits).",
  "Manual handoff": "No job is waiting for you to apply yourself. Jobs on sites that cannot be automated (LinkedIn, Naukri, most company forms) land here with their documents and link.",
  "Failed / needs verification": "Nothing failed and nothing needs checking.",
};
export const tabLabel = (tab: string, count: number | null | undefined): string => (count == null ? tab : `${tab} (${count})`);
/** Queue tasks that count as "queued & processing" (the same filter the queue tab uses). */
export const activeTasks = <T extends { status?: string }>(tasks: T[] | null | undefined): T[] => (tasks ?? []).filter((t) => ["queued", "processing", "paused"].includes(t.status ?? ""));

/** The API takes repeated ?states=A&states=B parameters (api.ts joins arrays, so the query is built here). */
export function applicationsPagePath(states: string[], limit = 25): string {
  return `/api/applications/page?${states.map((s) => `states=${encodeURIComponent(s)}`).join("&")}&dismissed=false&limit=${limit}`;
}

export const OPEN_STATES = new Set(["READY_FOR_REVIEW", "AWAITING_USER_INPUT", "APPROVED", "MANUAL_HANDOFF_REQUIRED", "SUBMISSION_FAILED", "VERIFICATION_PENDING"]);

/** Order of the job selector: best match score first, unscored last, otherwise as listed. It deliberately ignores application state, so the
 *  options never move when an application changes state (open applications are reached through the "Needs your action" tab). */
export function orderJobs<T extends { job_id: string }>(jobs: T[], scoreOf: (id: string) => number | null | undefined): T[] {
  return jobs.map((j, i) => ({ j, i })).sort((a, b) => (scoreOf(b.j.job_id) ?? -1) - (scoreOf(a.j.job_id) ?? -1) || a.i - b.i).map((x) => x.j);
}

/** The label depends ONLY on the job and its score - never on application state - so the selection cannot reset when the state changes. */
export function jobLabel(job: { job_title?: string | null; company_name?: string | null }, match?: { match_score?: number | null } | null): string {
  const base = `${job.job_title || "(untitled)"} - ${job.company_name || "(unknown)"}`;
  return match && match.match_score !== null && match.match_score !== undefined ? `${base}  [${Number(match.match_score)}]` : base;
}

/** Eligibility string from a match record (string, or {status}). */
export function eligibilityOf(match: any): string {
  const e = match?.eligibility ?? match?.eligibility_status ?? "—";
  return typeof e === "string" ? e : (e?.status ?? "—");
}

export const NO_PREPARE_STATES = ["SUBMITTING", "SUBMITTED", "VERIFICATION_PENDING", "VERIFIED"];
export const canPrepareAgain = (app: any | null): boolean => !(app && NO_PREPARE_STATES.includes(app.state));
export const HANDOFF_STATES = ["DISCOVERED", "READY_FOR_REVIEW", "AWAITING_USER_INPUT", "APPROVED", "SUBMISSION_FAILED"];
export const canSwitchToHandoff = (app: any): boolean => HANDOFF_STATES.includes(app.state);
export const canReject = (app: any): boolean => !NO_PREPARE_STATES.includes(app.state);
export const showAnswers = (app: any, todo: any[]): boolean => todo.length > 0 && ["AWAITING_USER_INPUT", "READY_FOR_REVIEW", "MANUAL_HANDOFF_REQUIRED"].includes(app.state);
export const showRecheck = (app: any): boolean => ["AWAITING_USER_INPUT", "READY_FOR_REVIEW", "APPROVED"].includes(app.state);

export const warningsOf = (app: any): any[] => ((app?.validation?.checks ?? []) as any[]).filter((c) => c.status === "warn");
/** Approve is only offered in READY_FOR_REVIEW; with warnings it stays disabled until they are acknowledged. */
export const showApprove = (app: any): boolean => app.state === "READY_FOR_REVIEW";
export const approveDisabled = (warnCount: number, acknowledged: boolean): boolean => warnCount > 0 && !acknowledged;
/** Submit needs the explicit tick. */
export const submitDisabled = (understood: boolean, busy: boolean): boolean => !understood || busy;

/** Only answers the candidate actually gave are sent; empty / unticked fields are left out (never auto-answered). */
export function answersPayload(values: Record<string, string | boolean | null | undefined>): Record<string, string | boolean> {
  const out: Record<string, string | boolean> = {};
  for (const [k, v] of Object.entries(values)) if (v !== "" && v !== null && v !== undefined && v !== false) out[k] = v;
  return out;
}

/** Links from the API are only ever opened when they are plain http(s); anything else (javascript:, data:, …) is dropped. */
export function safeHttpUrl(url: unknown): string | null {
  if (typeof url !== "string") return null;
  try { const u = new URL(url); return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null; } catch { return null; }
}

export function confirmBody(submitted: boolean, reference: string, notes: string) {
  return { submitted, reference: reference || null, notes: notes || null };
}
export const eventWhen = (at: unknown): string => String(at).slice(0, 19).replace("T", " ");
