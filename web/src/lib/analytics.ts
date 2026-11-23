// Pure logic for the Analytics page (port of frontend/pages/08_analytics.py + match_chart.py). No React; unit-tested.
export const QUICK_RANGES: Record<string, number | null> = { Custom: null, "Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90 };
export const EXPORTS: Array<[string, string]> = [["daily", "Daily"], ["weekly", "Weekly"], ["status", "By status"], ["sources", "Sources"], ["scores", "Scores"]];
export const COLUMN_NAMES: Record<string, string> = {
  date: "Date", week: "Week", jobs_discovered: "Discovered", jobs_matched: "Matched", applications_prepared: "Prepared", applications_submitted: "Submitted",
  applications_failed: "Failed", interviews: "Interviews",
};

export const toYmd = (d: Date): string => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export function addDays(ymd: string, n: number): string {
  const [y, m, d] = ymd.split("-").map(Number);
  return toYmd(new Date(y, m - 1, d + n));
}
/** [from, to] for a quick range ending today; null for Custom. */
export function quickRange(preset: string, today: string): [string, string] | null {
  const n = QUICK_RANGES[preset];
  return n ? [addDays(today, -(n - 1)), today] : null;
}
export const fmtPct = (v: number | null | undefined): string => (v === null || v === undefined ? "—" : `${Number(v.toPrecision(6))}%`);

export const isEmptyRange = (totals: Record<string, number>): boolean => !Object.values(totals).some(Boolean);
export const scoreChartData = (dist: Array<{ range: string; jobs: number }>) => dist.map((d) => ({ range: d.range, jobs: d.jobs }));
export const statusChartData = (by: Record<string, number>) => Object.entries(by).sort(([a], [b]) => a.localeCompare(b)).map(([status, applications]) => ({ status, applications }));
/** Rename API keys to the display headings, keeping only known columns first (in the order received). */
export function renameColumns(rows: any[]): { headers: string[]; keys: string[] } {
  const keys = rows.length ? Object.keys(rows[0]) : [];
  return { keys, headers: keys.map((k) => COLUMN_NAMES[k] ?? k) };
}
export const exportName = (kind: string, from: string, to: string): string => `jobpilot_${kind}_${from}_${to}.csv`;
