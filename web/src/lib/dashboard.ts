/** Dashboard display logic: the "Today" summary and which status boxes are worth a full card. Pure functions; every number comes from the API. */

export interface ActivityDay { date: string; jobs_discovered?: number; applications_prepared?: number; applications_submitted?: number }
export interface MatchCounts { good_matches?: number; to_apply?: number; eligible_not_applied?: number; applied?: number }
export type Tone = "info" | "success" | "warning";
export interface Headline { tone: Tone; text: string; link?: { to: string; label: string } }
export interface StatusItem { label: string; value: number; help?: string; to?: string }

/** Sum of one activity field over the last `n` days of the series (the series is oldest -> newest, one row per day). */
export function sumLastDays(series: Array<Partial<ActivityDay> & Record<string, unknown>> | undefined, key: keyof Omit<ActivityDay, "date">, n = 7): number {
  return (series ?? []).slice(-n).reduce((t, r) => t + (Number(r[key]) || 0), 0);
}

/** When the next daily job search runs (local time text from /api/scheduler/status), or null if the scheduler is off/paused/unknown. */
export function nextDiscovery(status: any): string | null {
  if (!status?.enabled || status?.paused) return null;
  const job = (status.jobs ?? []).find((j: any) => j?.name === "daily_discovery");
  return job?.next_run_local || job?.next_run_at || null;
}

/** The one line at the top: the most useful thing to do now. Order: things only you can do > good jobs to apply to > nothing waiting. */
export function headline(pendingTotal: number, counts: MatchCounts | null | undefined, next: string | null, fmt: (v: string) => string = (v) => v): Headline {
  if (pendingTotal > 0) {
    return { tone: "warning", text: `${pendingTotal} application${pendingTotal === 1 ? " needs" : "s need"} you (input, approval or a manual step).`, link: { to: "/application-queue", label: "Open Application Queue" } };
  }
  const toApply = counts?.to_apply ?? 0;
  if (toApply > 0) {
    return { tone: "info", text: `${toApply} good job${toApply === 1 ? "" : "s"} you have not applied to yet.`, link: { to: "/job-matching", label: "See them in Job Matching" } };
  }
  const when = next ? ` Next job search: ${fmt(next)}.` : " The daily job search is off - start it on the Automation page.";
  const eligible = counts?.eligible_not_applied ?? 0;
  const extra = eligible > 0 ? ` ${eligible} eligible job${eligible === 1 ? "" : "s"} below your score rule ${eligible === 1 ? "is" : "are"} in Job Matching.` : "";
  return { tone: "success", text: `Nothing is waiting for you right now.${when}${extra}`, link: eligible > 0 ? { to: "/job-matching", label: "Open Job Matching" } : undefined };
}

/** Status boxes split into the ones with something in them (shown as cards) and the zeros (listed on one quiet line). */
export function splitByValue(items: StatusItem[]): { shown: StatusItem[]; zero: StatusItem[] } {
  return { shown: items.filter((i) => i.value > 0), zero: items.filter((i) => !(i.value > 0)) };
}

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** "2026-11-24" -> "24 Nov" (the activity days are local calendar dates from the API; no time-zone shift here). */
export function shortDay(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  return m ? `${Number(m[3])} ${MON[Number(m[2]) - 1]}` : iso;
}

type Series = Array<Partial<ActivityDay> & Record<string, unknown>> | undefined;
export interface ActivitySummary { days: number; found: number; prepared: number; applied: number; busiest: { date: string; jobs: number } | null }

/** Totals for the activity window and the day with the most jobs found. */
export function activitySummary(series: Series): ActivitySummary {
  const rows = series ?? [];
  let busiest: ActivitySummary["busiest"] = null;
  for (const r of rows) {
    const n = Number(r.jobs_discovered) || 0;
    if (n > 0 && (!busiest || n > busiest.jobs)) busiest = { date: String(r.date), jobs: n };
  }
  return { days: rows.length, found: sumLastDays(rows, "jobs_discovered", rows.length), prepared: sumLastDays(rows, "applications_prepared", rows.length),
           applied: sumLastDays(rows, "applications_submitted", rows.length), busiest };
}

/** One plain sentence for the window, e.g. "Last 14 days: 334 jobs found, 31 applications prepared, 22 applied. Busiest day: 21 Nov (299 jobs found)." */
export function activitySentence(s: ActivitySummary): string {
  const n = (v: number, one: string, many: string) => `${v} ${v === 1 ? one : many}`;
  const head = `Last ${n(s.days, "day", "days")}: ${n(s.found, "job", "jobs")} found, ${n(s.prepared, "application", "applications")} prepared, ${s.applied} applied.`;
  return s.busiest ? `${head} Busiest day: ${shortDay(s.busiest.date)} (${n(s.busiest.jobs, "job", "jobs")} found).` : head;
}
