// Pure helpers converting between profile JSON and editable table rows (port of frontend/components/profile_editor.py). No React: unit-tested.
// List-valued cells are stored in one text cell, items separated by " ;; ".
export const SEP = " ;; ";

export type Kind = "str" | "list" | "bool" | "int";
export type Spec = ReadonlyArray<readonly [string, Kind]>;
export type Row = Record<string, string | boolean>;

export const EXPERIENCE_SPEC: Spec = [
  ["company_name", "str"], ["job_title", "str"], ["employment_type", "str"], ["location", "str"], ["start_date", "str"], ["end_date", "str"], ["is_current", "bool"],
  ["description", "str"], ["responsibilities", "list"], ["technologies_used", "list"], ["achievements", "list"],
];
export const PROJECT_SPEC: Spec = [
  ["project_name", "str"], ["description", "str"], ["role", "str"], ["project_url", "str"], ["technologies", "list"], ["responsibilities", "list"], ["achievements", "list"],
];
export const EDUCATION_SPEC: Spec = [
  ["degree", "str"], ["specialization", "str"], ["institution", "str"], ["location", "str"], ["start_year", "int"], ["graduation_year", "int"], ["grade", "str"],
];
export const CERT_SPEC: Spec = [
  ["certification_name", "str"], ["issuing_organization", "str"], ["issue_date", "str"], ["expiry_date", "str"], ["credential_id", "str"], ["credential_url", "str"],
];
export const SKILL_CATEGORIES = [
  "programming_languages", "machine_learning", "deep_learning", "generative_ai", "llm_frameworks", "libraries", "databases", "cloud_platforms", "developer_tools", "other_skills",
] as const;

const blank = (v: unknown): boolean => v === null || v === undefined || (typeof v === "string" && v.trim() === "") || (typeof v === "number" && Number.isNaN(v));
// Python's float()/int(float()) accept plain decimal numbers (not hex, not "1_0"): mirror that so the same inputs behave the same.
const DECIMAL = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;

export function toRows(items: any[] | null | undefined, spec: Spec): Row[] {
  return (items ?? []).map((item) => {
    const row: Row = {};
    for (const [field, kind] of spec) {
      const v = item?.[field];
      if (kind === "list") row[field] = ((v ?? []) as string[]).join(SEP);
      else if (kind === "bool") row[field] = Boolean(v);
      else if (kind === "int") row[field] = v === null || v === undefined ? "" : String(v);
      else row[field] = v || "";
    }
    return row;
  });
}

/** Rows from the editor -> list of objects for the API. Fully empty rows are dropped. */
export function fromRows(rows: Array<Record<string, any>>, spec: Spec): Array<Record<string, any>> {
  const out: Array<Record<string, any>> = [];
  for (const row of rows) {
    const item: Record<string, any> = {};
    let hasContent = false;
    for (const [field, kind] of spec) {
      const v = row[field];
      if (kind === "bool") {
        item[field] = !blank(v) ? Boolean(v) : false;
      } else if (kind === "list") {
        const items = blank(v) ? [] : String(v).split(";;").map((p) => p.trim()).filter(Boolean);
        item[field] = items;
        hasContent ||= items.length > 0;
      } else if (kind === "int") {
        if (blank(v)) item[field] = null;
        else {
          const s = String(v).trim();
          item[field] = DECIMAL.test(s) ? Math.trunc(Number(s)) : s;      // garbage is passed through so the API reports a clear 422
          hasContent = true;
        }
      } else {
        item[field] = blank(v) ? null : String(v).trim();
        hasContent ||= item[field] !== null;
      }
    }
    if (hasContent) out.push(item);
  }
  return out;
}

export const linesToList = (text: string | null | undefined): string[] => (text ?? "").replaceAll(",", "\n").split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
export const listToLines = (items: string[] | null | undefined): string => (items ?? []).join("\n");
const splitLines = (text: string): string[] => text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);

/** '' -> null; '3.5' -> 3.5; garbage is returned as-is so the API rejects it. */
export function parseYears(text: string | null | undefined): number | null | string {
  const t = (text ?? "").trim();
  if (!t) return null;
  return DECIMAL.test(t) ? Number(t) : t;
}

// ----------------------------------------------------------------------------------------------- whole-form <-> profile
export interface ProfileForm {
  contact: Record<"full_name" | "email" | "phone" | "location" | "linkedin_url" | "github_url" | "portfolio_url", string>;
  summary: string; title: string; years: string; domains: string; objective: string;
  skills: Record<string, string>;
  experience: Row[]; projects: Row[]; education: Row[]; certifications: Row[];
  achievements: string; reviewed: boolean;
}

export function profileToForm(p: any): ProfileForm {
  const c = p.contact ?? {}, s = p.summary ?? {}, sk = p.technical_skills ?? {};
  return {
    contact: { full_name: c.full_name || "", email: c.email || "", phone: c.phone || "", location: c.location || "", linkedin_url: c.linkedin_url || "", github_url: c.github_url || "", portfolio_url: c.portfolio_url || "" },
    summary: s.professional_summary || "", title: s.current_job_title || "", years: s.total_experience_years === null || s.total_experience_years === undefined ? "" : String(s.total_experience_years),
    domains: listToLines(s.primary_domains), objective: s.career_objective || "",
    skills: Object.fromEntries(SKILL_CATEGORIES.map((cat) => [cat, listToLines(sk[cat])])),
    experience: toRows(p.work_experience, EXPERIENCE_SPEC), projects: toRows(p.projects, PROJECT_SPEC), education: toRows(p.education, EDUCATION_SPEC), certifications: toRows(p.certifications, CERT_SPEC),
    achievements: listToLines(p.achievements), reviewed: p.review_status === "reviewed",
  };
}

/** The exact PUT body. Nothing is added or inferred: blank stays blank (the API stores None). */
export function formToPayload(f: ProfileForm): Record<string, any> {
  return {
    contact: { ...f.contact },
    summary: { professional_summary: f.summary, current_job_title: f.title, total_experience_years: parseYears(f.years), primary_domains: splitLines(f.domains), career_objective: f.objective },
    technical_skills: Object.fromEntries(Object.entries(f.skills).map(([cat, txt]) => [cat, linesToList(txt)])),
    work_experience: fromRows(f.experience, EXPERIENCE_SPEC), projects: fromRows(f.projects, PROJECT_SPEC), education: fromRows(f.education, EDUCATION_SPEC), certifications: fromRows(f.certifications, CERT_SPEC),
    achievements: splitLines(f.achievements), review_status: f.reviewed ? "reviewed" : "pending_review",
  };
}

export const emptyRow = (spec: Spec): Row => Object.fromEntries(spec.map(([f, k]) => [f, k === "bool" ? false : ""]));
export const titleCase = (s: string): string => s.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());

// ----------------------------------------------------------------------------------------------- upload checks
export const ALLOWED_RESUME_EXT = [".pdf", ".docx"];
export const MAX_BODY_BYTES = 30 * 1024 * 1024;     // the backend's hard request-size limit; the stricter MAX_UPLOAD_MB is enforced (and explained) by the API
export function checkResumeFile(name: string, size: number): string | null {
  const lower = name.toLowerCase();
  if (!ALLOWED_RESUME_EXT.some((e) => lower.endsWith(e))) return "Only PDF or DOCX files can be uploaded.";
  if (size <= 0) return "That file is empty.";
  if (size > MAX_BODY_BYTES) return "That file is too large to upload.";
  return null;
}
