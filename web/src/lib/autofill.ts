// The JobPilot Autofill bookmark: what is copied for a job, and the bookmark itself. Pure functions (unit-tested).
// The bookmark's code is web/src/autofill/runner.js, included as text: it runs only on a page YOU open, when YOU click it, and makes no requests.
import RUNNER from "../autofill/runner.js?raw";

export const AUTOFILL_MARK = "JOBPILOT-AUTOFILL:";

export interface AutofillField { key: string; label: string; value: string; derived?: boolean; note?: string | null }
export interface AutofillData {
  job: { job_id?: string; title?: string | null; company?: string | null; apply_url?: string | null };
  fields: AutofillField[]; answers: Array<{ question: string; answer: string }>; missing: string[]; problems: string[];
}

/** The text put on the clipboard: a marker + compact JSON (no line breaks, so it survives the one-line paste box). */
export function autofillPayload(d: AutofillData): string {
  const fields: Record<string, string> = {};
  for (const f of d.fields) if (f.value && f.value.trim()) fields[f.key] = f.value;
  const body = {
    v: 1, job: { title: d.job.title ?? null, company: d.job.company ?? null }, fields,
    derived: d.fields.filter((f) => f.derived).map((f) => f.key), answers: d.answers.filter((a) => a.question && a.answer),
  };
  return AUTOFILL_MARK + JSON.stringify(body);
}

/** One line for the screen after copying. */
export function autofillSummary(d: AutofillData): string {
  const n = d.fields.length, a = d.answers.length;
  return `Copied ${n} detail${n === 1 ? "" : "s"}${a ? ` and ${a} screening answer${a === 1 ? "" : "s"}` : ""}.`;
}

/** The bookmark address. Set on the link through the DOM (React refuses javascript: links in JSX). */
export const bookmarkletHref = (code: string = RUNNER): string => `javascript:${encodeURIComponent(code)}`;
export const RUNNER_SOURCE: string = RUNNER;
