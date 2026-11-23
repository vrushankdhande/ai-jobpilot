// Pure helpers for the Settings page (ports of the inline helpers in frontend/pages/09_settings.py). No React.
export const split = (text: string): string[] => text.replaceAll("\n", ",").split(",").map((x) => x.trim()).filter(Boolean);
export const joinList = (xs: string[] | undefined): string => (xs ?? []).join(", ");

export type Tri = "unknown" | "yes" | "no";
export const triToValue = (v: Tri): boolean | null => ({ unknown: null, yes: true, no: false } as const)[v];
export const valueToTri = (v: boolean | null | undefined): Tri => (v === true ? "yes" : v === false ? "no" : "unknown");

export const weightsTotal = (w: Record<string, number>): number => Object.values(w).reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0);
export const weightsValid = (w: Record<string, number>): boolean => Math.abs(weightsTotal(w) - 100) < 1e-6;

/** "09:00-21:00" -> ["09:00","21:00"]; anything else is sent as a single item so the server rejects it with a clear message. */
export function parseWindow(text: string): string[] {
  const parts = text.split("-");
  return parts.length === 2 ? parts : [text];
}
export const windowText = (w: string[] | undefined): string => (w ?? []).join("-");

export interface Prefs {
  titles: string; locations: string; skills: string; companies: string; remote: Tri; hybrid: Tri; onsite: Tri; reloc: Tri; xmin: number; xmax: number; sal: number; cur: string;
  notice: string; posted: number; types: string; auth: string; kin: string; kex: string;
  remoteIn: boolean;   // remote jobs must also be in the preferred locations (drops e.g. "Remote - United States")
}
export function prefsFromConfig(p: any): Prefs {
  return {
    titles: joinList(p.titles), locations: joinList(p.locations), skills: joinList(p.skills), companies: joinList(p.companies), remote: valueToTri(p.remote), hybrid: valueToTri(p.hybrid),
    onsite: valueToTri(p.onsite), reloc: valueToTri(p.open_to_relocation), xmin: Number(p.experience_years_min || 0), xmax: Number(p.experience_years_max || 0), sal: Number(p.min_salary || 0),
    cur: p.salary_currency || "", notice: p.notice_period || "", posted: Number(p.posted_within_days || 0), types: joinList(p.employment_types), auth: joinList(p.work_authorization),
    kin: joinList(p.keywords_include), kex: joinList(p.keywords_exclude), remoteIn: Boolean(p.remote_within_locations),
  };
}
/** Empty/zero means "unset" (null), never a guessed value. */
export function prefsToValues(f: Prefs): Record<string, unknown> {
  return {
    titles: split(f.titles), locations: split(f.locations), skills: split(f.skills), companies: split(f.companies), remote: triToValue(f.remote), hybrid: triToValue(f.hybrid), onsite: triToValue(f.onsite),
    open_to_relocation: triToValue(f.reloc), experience_years_min: f.xmin || null, experience_years_max: f.xmax || null, min_salary: f.sal || null, salary_currency: f.cur.trim().toUpperCase() || null,
    notice_period: f.notice.trim() || null, posted_within_days: Math.trunc(f.posted) || null, employment_types: split(f.types), work_authorization: split(f.auth),
    keywords_include: split(f.kin), keywords_exclude: split(f.kex), remote_within_locations: f.remoteIn,
  };
}

export const num = (s: string, fallback = 0): number => { const n = Number(s); return Number.isFinite(n) ? n : fallback; };
