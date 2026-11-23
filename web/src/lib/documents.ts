// Pure helpers that turn the editor's text boxes back into document content (port of frontend/components/document_editor.py). No React.
//
// Editing never touches contact details, employers, titles, dates, education or certifications - those must match the verified profile (correct them in
// Resume Manager). Bullets/answers the candidate leaves unchanged keep their profile evidence.
export const PLACEHOLDER = "[CANDIDATE INPUT REQUIRED";

export const lines = (text: string | null | undefined): string[] => (text ?? "").split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
export const bulletsText = (bullets: Array<{ text: string }> | undefined): string => (bullets ?? []).map((b) => b.text).join("\n");
export const skillsText = (items: string[] | undefined): string => (items ?? []).join("\n");

type Bullet = { text: string; source: unknown };
function mergeBullets(old: Bullet[] | undefined, newLines: string[]): Bullet[] {
  const byText = new Map((old ?? []).map((b) => [b.text, b]));
  return newLines.map((t) => (byText.has(t) ? { ...byText.get(t)! } : { text: t, source: null }));
}
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

export function applyResumeEdits(content: any, e: { summary: string; skills: Record<number, string>; experience: Record<number, string>; projects: Record<number, string>; achievements: string }): any {
  const c = clone(content);
  c.summary = (e.summary ?? "").trim();
  c.summary_sources = c.summary === (content.summary ?? "").trim() ? content.summary_sources ?? [] : [];
  for (const [i, text] of Object.entries(e.skills)) c.skills[Number(i)].items = lines(text);
  c.skills = c.skills.filter((g: any) => g.items.length > 0);
  for (const [i, text] of Object.entries(e.experience)) c.experience[Number(i)].bullets = mergeBullets(content.experience[Number(i)].bullets, lines(text));
  for (const [i, text] of Object.entries(e.projects)) c.projects[Number(i)].bullets = mergeBullets(content.projects[Number(i)].bullets, lines(text));
  c.achievements = mergeBullets(content.achievements, lines(e.achievements));
  return c;
}

export function applyLetterEdits(content: any, paragraphs: string[], signature?: string | null): any {
  const c = clone(content);
  const old = new Map((content.paragraphs as any[]).map((p) => [p.text, p]));
  c.paragraphs = paragraphs.map((p) => p.trim()).filter(Boolean).map((t) => (old.has(t) ? { ...old.get(t) } : { text: t, sources: [] }));
  if (signature !== undefined && signature !== null) c.signature = signature.trim() || null;
  return c;
}

/** A needs_input answer the candidate has written becomes 'answered'; a still-empty one stays needs_input. */
export function applyScreeningEdits(content: any, answers: string[]): any {
  const c = clone(content);
  (c.answers as any[]).forEach((a, i) => {
    if (i >= answers.length) return;
    const text = (answers[i] ?? "").trim();
    if (text === String(a.answer ?? "").trim()) return;
    a.answer = text;
    if (!text || text.includes(PLACEHOLDER)) { a.status = "needs_input"; a.sources = []; }
    else { a.status = "answered"; a.sources = ["candidate_input"]; a.note = null; }
  });
  return c;
}

export const issueIcon = (severity: string): string => ({ error: "🛑", warning: "⚠️", info: "ℹ️" } as Record<string, string>)[severity] ?? "•";
export const statusBadge = (status: string): string => ({
  passed: "✅ passed", passed_with_warnings: "⚠️ passed with warnings", failed: "🛑 failed", not_validated: "⏳ not validated",
  draft: "📝 draft", approved: "✅ approved", superseded: "📦 superseded",
} as Record<string, string>)[status] ?? status;

/** Approval rules shown by the page (the backend enforces them again): failed blocks; warnings need an explicit acknowledgement. */
export function approvalState(doc: { review_status: string; validation: { status: string } }, acknowledged: boolean): { approved: boolean; blocked: boolean; needsAck: boolean; canApprove: boolean } {
  const approved = doc.review_status === "approved";
  const blocked = doc.validation.status === "failed";
  const needsAck = doc.validation.status === "passed_with_warnings";
  return { approved, blocked, needsAck, canApprove: !approved && !blocked && (!needsAck || acknowledged) };
}

/** Candidate inputs for screening answers: only non-empty values are sent (never guessed). */
export function candidateInputs(inputs: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(inputs).filter(([, v]) => v.trim() !== ""));
}
