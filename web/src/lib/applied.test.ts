import { appliedPath, appliedRows, buildAppliedParams, canRecordOutcome, confirmBody, eventRows, screenshotUrl } from "./applied";

describe("applied jobs helpers", () => {
  it("hides rejected applications except under Everything", () => {
    expect(buildAppliedParams("Failed", "", "", "")).toEqual({ params: { dismissed: false }, states: ["SUBMISSION_FAILED"] });
    expect(buildAppliedParams("Everything", "", "", "")).toEqual({ params: {}, states: [] });
  });

  it("adds search and an inclusive end date", () => {
    const { params } = buildAppliedParams("Everything", "acme", "2026-11-01", "2026-11-30");
    expect(params).toEqual({ q: "acme", date_from: "2026-11-01T00:00:00", date_to: "2026-12-01T00:00:00" });
  });

  it("repeats the states parameter for the list query", () => {
    expect(appliedPath("/api/applications/page", { limit: 1, dismissed: false }, ["SUBMITTED", "VERIFIED"])).toBe("/api/applications/page?limit=1&dismissed=false&states=SUBMITTED&states=VERIFIED");
    expect(appliedPath("/x", {}, [])).toBe("/x");
  });

  it("builds rows with the submitted date or created date", () => {
    const r = appliedRows([{ application_id: "a", company_name: "Co", job_title: "T", submitted_at: null, created_at: "2026-11-05T10:00:00", state: "VERIFIED", mode: "manual_handoff", source: "linkedin", user_notes: "x".repeat(80), dismissed_at: "2026-11-06" }]);
    expect(r[0]).toMatchObject({ company: "Co", date: "05 Nov 2026", status: "🏁 Verified", how: "manual", platform: "linkedin", rejected: "yes" });
    expect(r[0].notes).toHaveLength(60);
  });

  it("offers the outcome form only for uncertain, manual or submitted states", () => {
    for (const s of ["VERIFICATION_PENDING", "MANUAL_HANDOFF_REQUIRED", "SUBMITTED"]) expect(canRecordOutcome(s)).toBe(true);
    for (const s of ["APPROVED", "READY_FOR_REVIEW", "VERIFIED", "SUBMISSION_FAILED"]) expect(canRecordOutcome(s)).toBe(false);
  });

  it("records 'submitted' for SUBMITTED regardless of the radio, and blanks become null", () => {
    expect(confirmBody("SUBMITTED", "It was NOT submitted", "", "")).toEqual({ submitted: true, reference: null, notes: null });
    expect(confirmBody("VERIFICATION_PENDING", "It was NOT submitted", "R1", "mail")).toEqual({ submitted: false, reference: "R1", notes: "mail" });
    expect(confirmBody("MANUAL_HANDOFF_REQUIRED", "I submitted it", "", "")).toMatchObject({ submitted: true });
  });

  it("formats event rows and encodes screenshot names", () => {
    expect(eventRows([{ seq: 1, at: "2026-11-05T10:00:00", type: "answers_updated", from_state: null, to_state: "X", actor: "candidate" }])[0]).toMatchObject({ seq: 1, event: "answers updated", from: "", to: "X", by: "candidate" });
    expect(screenshotUrl("a1", "x y.png")).toBe("/api/applications/a1/screenshots/x%20y.png");
  });
});
