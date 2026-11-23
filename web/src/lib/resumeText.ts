// Plain-text view of a generated document (port of engines/generation/render.py: presentation only - the same text the exports use). No React.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** '2022-01' -> 'Jan 2022'; '2019' -> '2019'. Never invents a month. */
export function monthName(value: string | null | undefined): string | null {
  if (!value) return null;
  if (value.length >= 7 && value[4] === "-" && /^\d\d$/.test(value.slice(5, 7)) && Number(value.slice(5, 7)) >= 1 && Number(value.slice(5, 7)) <= 12) return `${MONTHS[Number(value.slice(5, 7)) - 1]} ${value.slice(0, 4)}`;
  return value;
}
export function formatPeriod(start: string | null | undefined, end: string | null | undefined, current: boolean): string {
  const s = monthName(start), e = current ? "Present" : monthName(end);
  return s && e ? `${s} - ${e}` : s || e || "";
}
const join = (xs: Array<unknown>, sep: string): string => xs.filter((x) => x).join(sep);

export function resumeLines(c: any): string[] {
  const h = c.header ?? {};
  const lines: string[] = [`# ${h.full_name || ""}`.trim(), h.headline || "", join([h.email, h.phone, h.location, h.linkedin_url, h.github_url, h.portfolio_url], " | ")];
  if (c.summary) lines.push("## Summary", c.summary);
  if (c.skills?.length) { lines.push("## Skills"); for (const g of c.skills) lines.push(`${g.label}: ${(g.items ?? []).join(", ")}`); }
  if (c.experience?.length) {
    lines.push("## Experience");
    for (const e of c.experience) { lines.push(`${e.job_title || ""} - ${e.company_name || ""} (${formatPeriod(e.start_date, e.end_date, Boolean(e.is_current))})`); for (const b of e.bullets ?? []) lines.push(`- ${b.text}`); }
  }
  if (c.projects?.length) {
    lines.push("## Projects");
    for (const p of c.projects) { lines.push(`${p.project_name || ""} [${(p.technologies ?? []).join(", ")}]`); for (const b of p.bullets ?? []) lines.push(`- ${b.text}`); }
  }
  if (c.education?.length) { lines.push("## Education"); for (const e of c.education) lines.push(join([e.degree, e.specialization, e.institution, e.graduation_year ? String(e.graduation_year) : "", e.grade], " - ")); }
  if (c.certifications?.length) { lines.push("## Certifications"); for (const x of c.certifications) lines.push(join([x.certification_name, x.issuing_organization, monthName(x.issue_date)], " - ")); }
  if (c.achievements?.length) { lines.push("## Achievements"); for (const a of c.achievements) lines.push(`- ${a.text}`); }
  return lines;
}
export const coverLetterLines = (c: any): string[] => [c.salutation, "", ...(c.paragraphs ?? []).map((p: any) => p.text), "", c.closing, c.signature || ""];
export function screeningLines(c: any): string[] {
  const out: string[] = [];
  for (const a of c.answers ?? []) out.push(`Q: ${a.question}`, `A [${a.status}]: ${a.answer}`, "");
  return out;
}
export function renderLines(docType: string, content: any): string[] {
  if (docType === "resume") return resumeLines(content);
  if (docType === "cover_letter") return coverLetterLines(content);
  if (docType === "screening_answers") return screeningLines(content);
  return [];
}
