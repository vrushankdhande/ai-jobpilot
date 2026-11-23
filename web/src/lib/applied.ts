// Pure logic for the Applied Jobs page (port of frontend/pages/06_applied_jobs.py). No React; unit-tested.
import { fmtDt } from "../format";
import { stateLabel } from "./applications";
import { dayStartIso, nextDayIso } from "./discovery";

export const PRESETS: Record<string, string[] | null> = {
  "Applied (submitted + verified)": ["SUBMITTED", "VERIFIED"], "Outcome unknown": ["VERIFICATION_PENDING"], Failed: ["SUBMISSION_FAILED"], "Manual handoff": ["MANUAL_HANDOFF_REQUIRED"],
  "Needs attention (all of the above except applied)": ["VERIFICATION_PENDING", "SUBMISSION_FAILED", "MANUAL_HANDOFF_REQUIRED"], Everything: null,
};
export const PAGE_SIZE = 15;

/** Query params for /api/applications/page. `states` is a list (the fetch layer repeats nothing: it is joined as the API expects). */
export function buildAppliedParams(preset: string, q: string, from: string, to: string): { params: Record<string, string | boolean>; states: string[] } {
  const params: Record<string, string | boolean> = {};
  const states = PRESETS[preset] ?? [];
  if (q) params.q = q;
  if (from) params.date_from = dayStartIso(from);
  if (to) params.date_to = nextDayIso(to);
  if (preset !== "Everything") params.dismissed = false;
  return { params, states };
}
/** Builds the path with a repeated `states` parameter (FastAPI list query) without touching the shared API client. */
export function appliedPath(base: string, params: Record<string, string | number | boolean>, states: string[]): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) sp.set(k, String(v));
  for (const s of states) sp.append("states", s);
  const qs = sp.toString();
  return qs ? `${base}?${qs}` : base;
}

export function appliedRows(rows: any[]) {
  return rows.map((r) => ({
    id: r.application_id, company: r.company_name || "", title: r.job_title || "", date: fmtDt(r.submitted_at || r.created_at, false), status: stateLabel(r.state),
    how: r.mode === "automated" ? "automated" : "manual", platform: r.provider || r.source || "", reference: r.reference || "", confirmedBy: r.verified_by || "",
    notes: (r.user_notes || "").slice(0, 60), rejected: r.dismissed_at ? "yes" : "",
  }));
}
export function eventRows(events: any[]) {
  return events.map((e) => ({ seq: e.seq, when: fmtDt(e.at), event: String(e.type).replaceAll("_", " "), from: e.from_state || "", to: e.to_state || "", by: e.actor }));
}
/** The "record what you found" form exists only for uncertain / manual / submitted applications. */
export const canRecordOutcome = (state: string): boolean => ["VERIFICATION_PENDING", "MANUAL_HANDOFF_REQUIRED", "SUBMITTED"].includes(state);
export function confirmBody(state: string, what: string, ref: string, note: string) {
  return { submitted: state === "SUBMITTED" ? true : what === "I submitted it", reference: ref || null, notes: note || null };
}
export const screenshotUrl = (appId: string, name: string): string => `/api/applications/${encodeURIComponent(appId)}/screenshots/${encodeURIComponent(name)}`;
