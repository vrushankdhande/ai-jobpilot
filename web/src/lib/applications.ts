// Pure display helpers for the application pages (port of frontend/components/application_view.py). Unit-tested; no React.
export const STATE_LABELS: Record<string, [string, string]> = {
  DISCOVERED: ["🆕", "Discovered"], READY_FOR_REVIEW: ["👀", "Ready for your review"], AWAITING_USER_INPUT: ["✋", "Needs your input"],
  APPROVED: ["✅", "Approved - not submitted yet"], SUBMITTING: ["⏳", "Submitting…"], SUBMITTED: ["📨", "Submitted (confirmation seen)"],
  SUBMISSION_FAILED: ["🛑", "Not submitted (failed)"], VERIFICATION_PENDING: ["❓", "Outcome unknown - please verify"],
  VERIFIED: ["🏁", "Verified"], MANUAL_HANDOFF_REQUIRED: ["🧑‍💻", "You apply (manual handoff)"],
};
export const FIELD_STATUS: Record<string, string> = {
  filled: "✅ filled", ready: "✅ ready", needs_input: "✋ needs your input", manual_required: "✋ you must answer", optional_skipped: "➖ optional, left blank",
};
export const CHECK_ICON: Record<string, string> = { pass: "✅", fail: "🛑", warn: "⚠️" };
export const CHECK_TITLES: Record<string, string> = {
  platform_authorized: "Platform allows automation", application_matches_job: "Application belongs to this job", eligibility_acknowledged: "Eligibility acknowledged",
  form_detected: "Application form recognised", mandatory_fields_complete: "All mandatory fields completed", personal_information_correct: "Personal details match your profile",
  identity_present: "Name and email in your profile", documents_approved: "Approved resume available", resume_uploaded: "Resume uploaded and verified",
  cover_letter_attached: "Cover letter attached", screening_answers_approved: "Answers come from approved sources", no_validation_errors: "No validation errors on the form",
  review_suggested: "Values to double-check", optional_fields_blank: "Optional fields left blank", candidate_approval_recorded: "Your approval is recorded",
};

export function stateLabel(state: string): string {
  const [icon, text] = STATE_LABELS[state] ?? ["•", state];
  return `${icon} ${text}`;
}

export interface CheckRow { icon: string; check: string; critical: string; detail: string }
export function checkRows(validation: any): CheckRow[] {
  return ((validation?.checks ?? []) as any[]).map((c) => ({
    icon: CHECK_ICON[c.status] ?? "•", check: CHECK_TITLES[c.name] ?? (c.name.replaceAll("_", " ").replace(/^./, (s: string) => s.toUpperCase())),
    critical: c.critical ? "yes" : "no", detail: c.message || "",
  }));
}

export interface MappingRow { field: string; required: string; value: string; status: string; source: string; match: string; note: string }
export function mappingRows(mapping: any[] | undefined): MappingRow[] {
  return (mapping ?? []).map((m) => {
    const v = m.value;
    let shown = (m.canonical === "resume_upload" || m.canonical === "cover_letter_upload") && m.status === "filled" ? "(file attached)"
      : v === true ? "✔ ticked" : v === null || v === undefined || v === false ? "" : String(v);
    if (shown.length > 90) shown = shown.slice(0, 87) + "…";
    return {
      field: m.label || "", required: m.required ? "yes" : "", value: shown, status: FIELD_STATUS[m.status] ?? m.status, source: m.source || "",
      match: m.status === "filled" || m.status === "ready" ? (m.confidence ?? "") : "", note: (m.derived ? "⚠ derived - check. " : "") + (m.note || ""),
    };
  });
}

/** Fields the candidate must (or may) answer themselves. */
export const fieldsNeedingInput = (mapping: any[] | undefined): any[] => (mapping ?? []).filter((m) => m.status === "needs_input" || m.status === "manual_required");

/** Which input to show for a field that needs the candidate. */
export function answerWidget(m: any): "checkbox" | "select" | "text" {
  if (m.kind === "checkbox") return "checkbox";
  if ((m.kind === "select" || m.kind === "radio") && m.options?.length) return "select";
  return "text";
}

export function nextStep(app: any | null): string {
  if (!app) return "Prepare an application to see what will be filled in.";
  const map: Record<string, string> = {
    DISCOVERED: "Preparing…", AWAITING_USER_INPUT: "Provide the missing answers / approve the missing documents, then re-check.",
    READY_FOR_REVIEW: "Review everything below. Nothing is sent until you approve and then press Submit.",
    APPROVED: "Approved. Press Submit to send exactly what you approved (you can still switch to manual handoff).",
    SUBMITTING: "Submitting… do not press anything again.", SUBMITTED: "The site showed a confirmation. Add the reference number to mark it verified if there was one.",
    SUBMISSION_FAILED: "Nothing was sent. Fix the cause and prepare again, or apply yourself.",
    VERIFICATION_PENDING: "We cannot tell whether it went through. Check your email / the employer's site, then record what you found. It will NOT be retried automatically.",
    VERIFIED: "Done - this application is verified.",
    MANUAL_HANDOFF_REQUIRED: app.mode !== "automated" ? "Apply yourself using the prepared package, then record your confirmation." : "Apply yourself.",
  };
  return map[app.state] ?? "";
}

/** The Submit button exists ONLY for an approved, automated application. */
export const canShowSubmit = (app: any | null): boolean => Boolean(app && app.state === "APPROVED" && app.mode === "automated");
