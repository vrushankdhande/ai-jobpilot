// Pure display helpers for the dashboard and automation pages (port of frontend/components/automation_view.py). Unit-tested; no React.
export const QUEUE_LABELS: Record<string, string> = {
  queued: "⏳ Queued", processing: "⚙️ Processing", awaiting_approval: "👀 Awaiting your approval", completed: "🏁 Completed", failed: "🛑 Failed",
  manual_handoff: "🧑‍💻 Manual handoff", needs_verification: "❓ Needs verification", paused: "⏸️ Paused", cancelled: "🚫 Cancelled",
};
export const CONTROLLABLE: Record<string, string[]> = {
  pause: ["queued"], resume: ["paused"], cancel: ["queued", "paused", "processing", "awaiting_approval", "failed", "manual_handoff"],
};
export const canControl = (action: string, status: string): boolean => (CONTROLLABLE[action] ?? []).includes(status);

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function shortTime(value: unknown, tz = ""): string {
  if (!value) return "—";
  const d = new Date(String(value));
  if (Number.isNaN(d.getTime())) return String(value).slice(0, 16);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getDate())} ${MONTHS[d.getMonth()]} ${p(d.getHours())}:${p(d.getMinutes())}${tz ? ` ${tz}` : ""}`;
}
export const usageRatio = (used: number, limit: number): number => (limit <= 0 && used >= 0 ? 1 : Math.min(1, Math.max(0, used / limit)));

export interface LimitRow { limit: string; used: number; allowed: number; cap: number }
export function limitRows(status: any): LimitRow[] {
  const lim = status.limits.effective, usage = status.limits.usage, caps = status.limits.caps;
  return [
    { limit: "Jobs discovered today", used: usage.jobs_discovered ?? 0, allowed: lim.max_jobs_discovered_per_day, cap: caps.max_jobs_discovered_per_day },
    { limit: "Jobs prepared today", used: usage.jobs_processed ?? 0, allowed: lim.max_jobs_processed_per_day, cap: caps.max_jobs_processed_per_day },
    { limit: "Applications submitted per day", used: usage.applications_submitted ?? 0, allowed: lim.max_applications_per_day, cap: caps.max_applications_per_day },
  ];
}
export function queueRows(tasks: any[]) {
  return tasks.map((t) => ({
    job: t.job_title || "(untitled)", company: t.company_name || "", score: t.match_score, eligibility: t.eligibility || "", status: QUEUE_LABELS[t.status] ?? t.status,
    attempts: `${t.attempts ?? 0}/${t.max_attempts ?? 0}`, nextAttempt: t.status === "queued" ? shortTime(t.next_attempt_at) : "", lastError: t.last_error || "",
  }));
}
export function failureRows(tasks: any[]) {
  return tasks.filter((t) => t.status === "failed" || t.status === "needs_verification").map((t) => ({
    job: t.job_title || "(untitled)", company: t.company_name || "", status: QUEUE_LABELS[t.status] ?? t.status, attempts: t.attempts ?? 0, error: t.last_error || "", updated: shortTime(t.updated_at),
  }));
}
export function jobRows(status: any) {
  return (status.jobs as any[]).map((j) => ({
    job: j.name.replaceAll("_", " ").replace(/\b\w/g, (c: string) => c.toUpperCase()), schedule: j.schedule, lastRun: shortTime(j.last_run_at), nextRun: j.next_run_local ? shortTime(j.next_run_at) : "—",
    running: j.running ? "yes" : "", lastStatus: j.last_status || "—", lastError: j.last_error || "",
  }));
}
const NOTE_MARK: Record<string, string> = { sent: "✅ sent", failed: "🛑 failed", skipped: "➖ skipped (not configured)", queued: "⏳ queued" };
export function notificationRows(items: any[]) {
  return items.map((n) => ({ when: shortTime(n.created_at), type: String(n.type).replaceAll("_", " "), subject: n.subject, status: NOTE_MARK[n.status] ?? n.status, attempts: n.attempts ?? 0, note: n.error || "" }));
}
export function reportSummary(rep: any): Array<{ metric: string; value: string }> {
  const a = rep.applications, d = rep.discovery, m = rep.matching;
  const rows: Array<[string, unknown]> = [
    ["Jobs discovered", d.discovered], ["Duplicates removed", d.duplicates_removed], ["Jobs matched", m.matched], ["Excellent / Strong / Good", `${m.excellent} / ${m.strong} / ${m.good}`],
    ["Eligible opportunities", m.eligible], ["Awaiting your approval", a.awaiting_approval], ["Submitted (confirmation seen)", a.submitted], ["Verified", a.verified],
    ["Failed", a.failed], ["Manual handoffs", a.manual_handoff], ["Pending verification", a.pending_verification],
  ];
  return rows.map(([metric, value]) => ({ metric, value: String(value) }));
}
