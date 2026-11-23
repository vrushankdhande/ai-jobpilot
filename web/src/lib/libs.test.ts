import { describe, expect, it } from "vitest";
import { answerWidget, canShowSubmit, checkRows, fieldsNeedingInput, mappingRows, nextStep, stateLabel } from "./applications";
import { canControl, limitRows, usageRatio } from "./automation";

describe("application helpers (ported from application_view.py)", () => {
  it("labels states and passes unknown ones through", () => {
    expect(stateLabel("READY_FOR_REVIEW")).toBe("👀 Ready for your review");
    expect(stateLabel("MYSTERY")).toBe("• MYSTERY");
  });

  it("Submit is shown ONLY for an approved, automated application", () => {
    expect(canShowSubmit({ state: "APPROVED", mode: "automated" })).toBe(true);
    expect(canShowSubmit({ state: "APPROVED", mode: "manual_handoff" })).toBe(false);
    for (const s of ["DISCOVERED", "AWAITING_USER_INPUT", "READY_FOR_REVIEW", "SUBMITTING", "SUBMITTED", "SUBMISSION_FAILED", "VERIFICATION_PENDING", "VERIFIED", "MANUAL_HANDOFF_REQUIRED"]) {
      expect(canShowSubmit({ state: s, mode: "automated" })).toBe(false);
    }
    expect(canShowSubmit(null)).toBe(false);
  });

  it("says an uncertain outcome will not be retried automatically", () => {
    expect(nextStep({ state: "VERIFICATION_PENDING" })).toContain("NOT be retried automatically");
  });

  it("chooses the right input for candidate-only questions and never defaults a value", () => {
    expect(answerWidget({ kind: "checkbox" })).toBe("checkbox");
    expect(answerWidget({ kind: "select", options: ["Yes", "No"] })).toBe("select");
    expect(answerWidget({ kind: "radio", options: [] })).toBe("text");
    expect(answerWidget({ kind: "text" })).toBe("text");
    expect(fieldsNeedingInput([{ status: "filled" }, { status: "needs_input" }, { status: "manual_required" }])).toHaveLength(2);
  });

  it("builds mapping rows: files, ticks, long values", () => {
    const rows = mappingRows([
      { label: "Resume", canonical: "resume_upload", status: "filled", value: "x.pdf", required: true },
      { label: "Consent", status: "filled", value: true }, { label: "Blank", status: "optional_skipped", value: null },
      { label: "Long", status: "filled", value: "a".repeat(200), derived: true },
    ]);
    expect(rows[0].value).toBe("(file attached)");
    expect(rows[1].value).toBe("✔ ticked");
    expect(rows[2].value).toBe("");
    expect(rows[3].value).toHaveLength(88);
    expect(rows[3].note).toContain("derived");
  });

  it("titles validation checks", () => {
    const rows = checkRows({ checks: [{ name: "documents_approved", status: "pass", critical: true }, { name: "some_new_check", status: "warn", message: "m" }] });
    expect(rows[0]).toMatchObject({ icon: "✅", check: "Approved resume available", critical: "yes" });
    expect(rows[1]).toMatchObject({ icon: "⚠️", check: "Some new check", detail: "m" });
  });
});

describe("automation helpers (ported from automation_view.py)", () => {
  it("only allows sensible queue controls", () => {
    expect(canControl("pause", "queued")).toBe(true);
    expect(canControl("pause", "processing")).toBe(false);
    expect(canControl("resume", "paused")).toBe(true);
    expect(canControl("cancel", "completed")).toBe(false);
    expect(canControl("approve", "queued")).toBe(false);          // there is no approve action for the worker
  });
  it("computes usage ratios", () => {
    expect(usageRatio(5, 10)).toBe(0.5);
    expect(usageRatio(20, 10)).toBe(1);
    expect(usageRatio(0, 0)).toBe(1);
  });
  it("builds limit rows from the scheduler status", () => {
    const rows = limitRows({ limits: { effective: { max_jobs_discovered_per_day: 50, max_jobs_processed_per_day: 20, max_applications_per_day: 5 }, usage: { jobs_discovered: 3 }, caps: { max_jobs_discovered_per_day: 100, max_jobs_processed_per_day: 40, max_applications_per_day: 10 } } });
    expect(rows[0]).toEqual({ limit: "Jobs discovered today", used: 3, allowed: 50, cap: 100 });
    expect(rows[2].used).toBe(0);
  });
});
