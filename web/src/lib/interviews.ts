// Pure logic for the Interview Tracker page (unit-tested; no React).
export const KINDS = ["video", "phone", "onsite", "technical", "hr", "other"];
export const STATUSES = ["scheduled", "completed", "cancelled", "no_show"];
export const OUTCOMES = ["pending", "advanced", "rejected", "offer", "withdrawn"];
export const INTERVIEW_STATUS: Record<string, string> = { scheduled: "📅 scheduled", completed: "✅ completed", cancelled: "🚫 cancelled", no_show: "⚠️ no show" };
export const OUTCOME: Record<string, string> = { pending: "⏳ pending", advanced: "➡️ advanced", rejected: "❌ rejected", offer: "🎉 offer", withdrawn: "↩️ withdrawn" };
export const badge = (kind: Record<string, string>, value?: string | null): string => kind[value ?? ""] ?? (value || "—");

export interface AddForm {
  linkedApp: string; company: string; title: string; round: string; date: string; time: string; kind: string; duration: number | string;
  link: string; interviewers: string; remind: number | string; notes: string;
}
export const pad = (n: number) => String(n).padStart(2, "0");
export function tomorrow(now = new Date()): string {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
export const emptyAddForm = (now = new Date()): AddForm => ({
  linkedApp: "", company: "", title: "", round: "Round 1", date: tomorrow(now), time: "10:00", kind: "video", duration: 60, link: "", interviewers: "", remind: 60, notes: "",
});

/** Request body for POST /api/interviews. The wall-clock time the user typed is sent as is (local time, like the Streamlit page). */
export function addBody(f: AddForm) {
  const time = f.time.length === 5 ? `${f.time}:00` : f.time;
  const body: Record<string, unknown> = {
    company_name: f.company, job_title: f.title, round: f.round || "Round 1", kind: f.kind, scheduled_at: `${f.date}T${time}`, duration_minutes: Math.trunc(Number(f.duration)),
    meeting_link: f.link || null, interviewers: f.interviewers || null, preparation_notes: f.notes || null, reminder_minutes_before: Math.trunc(Number(f.remind)),
  };
  if (f.linkedApp) body.application_id = f.linkedApp;
  return body;
}

/** Split a stored (UTC) timestamp into the date/time shown in the reschedule fields. */
export function utcParts(iso: string): { date: string; time: string } {
  const d = new Date(/(Z|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`);
  return { date: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`, time: `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}` };
}

export interface EditForm { status: string; outcome: string; kind: string; date: string; time: string; link: string; prep: string; feedback: string }
export const editFormOf = (cur: any): EditForm => ({
  status: cur.status, outcome: cur.outcome, kind: cur.kind, ...utcParts(String(cur.scheduled_at)), link: cur.meeting_link || "", prep: cur.preparation_notes || "", feedback: cur.feedback || "",
});
/** Request body for PUT /api/interviews/{id}; scheduled_at is only sent when the time was actually changed. */
export function editBody(f: EditForm, original: EditForm) {
  const body: Record<string, unknown> = { status: f.status, outcome: f.outcome, kind: f.kind, meeting_link: f.link, preparation_notes: f.prep, feedback: f.feedback };
  if (f.date !== original.date || f.time !== original.time) body.scheduled_at = `${f.date}T${f.time.length === 5 ? `${f.time}:00` : f.time}+00:00`;
  return body;
}
export function listParams(f: { q: string; status: string; outcome: string; newest: boolean }): Record<string, string | boolean> {
  const p: Record<string, string | boolean> = { newest_first: f.newest };
  if (f.q) p.q = f.q;
  if (f.status !== "Any") p.status = f.status;
  if (f.outcome !== "Any") p.outcome = f.outcome;
  return p;
}
