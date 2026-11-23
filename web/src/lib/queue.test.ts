import { describe, expect, it } from "vitest";
import {
  answersPayload, applicationsPagePath, orderJobs, OVERVIEW_TABS, approveDisabled, canPrepareAgain, canReject, canSwitchToHandoff, confirmBody, eligibilityOf, jobLabel, safeHttpUrl, showAnswers, showApprove,
  showRecheck, submitDisabled, TAB_STATES, warningsOf,
} from "./queue";
import { canShowSubmit } from "./applications";

describe("job label", () => {
  it("contains only title, company and score - never the application state", () => {
    expect(jobLabel({ job_title: "ML Engineer", company_name: "Acme" }, { match_score: 77.5 })).toBe("ML Engineer - Acme  [77.5]");
    expect(jobLabel({ job_title: null, company_name: null }, null)).toBe("(untitled) - (unknown)");
    expect(jobLabel({ job_title: "A", company_name: "B" }, { match_score: 80 })).toBe("A - B  [80]");
    expect(jobLabel({ job_title: "A", company_name: "B" }, { match_score: 80 })).not.toMatch(/READY|AWAITING|APPROVED|·/);
  });
});

describe("eligibility", () => {
  it("reads strings and {status} objects and falls back to a dash", () => {
    expect(eligibilityOf({ eligibility: "FAIL" })).toBe("FAIL");
    expect(eligibilityOf({ eligibility_status: { status: "PASS" } })).toBe("PASS");
    expect(eligibilityOf(undefined)).toBe("—");
  });
});

describe("approval and submit gates", () => {
  const app = (state: string, mode = "automated") => ({ state, mode });
  it("Submit exists only for an APPROVED automated application", () => {
    expect(canShowSubmit(app("APPROVED"))).toBe(true);
    for (const s of ["DISCOVERED", "READY_FOR_REVIEW", "AWAITING_USER_INPUT", "SUBMITTING", "SUBMITTED", "VERIFICATION_PENDING", "VERIFIED", "SUBMISSION_FAILED", "MANUAL_HANDOFF_REQUIRED"]) expect(canShowSubmit(app(s))).toBe(false);
    expect(canShowSubmit(app("APPROVED", "manual_handoff"))).toBe(false);
    expect(canShowSubmit(null)).toBe(false);
  });
  it("submit is disabled until the explicit tick and while busy", () => {
    expect(submitDisabled(false, false)).toBe(true);
    expect(submitDisabled(true, true)).toBe(true);
    expect(submitDisabled(true, false)).toBe(false);
  });
  it("approve is offered only in READY_FOR_REVIEW and needs the warnings acknowledged", () => {
    expect(showApprove({ state: "READY_FOR_REVIEW" })).toBe(true);
    expect(showApprove({ state: "APPROVED" })).toBe(false);
    expect(approveDisabled(2, false)).toBe(true);
    expect(approveDisabled(2, true)).toBe(false);
    expect(approveDisabled(0, false)).toBe(false);
  });
  it("collects only the warn checks", () => {
    expect(warningsOf({ validation: { checks: [{ status: "pass" }, { status: "warn", name: "x" }] } })).toHaveLength(1);
    expect(warningsOf({})).toEqual([]);
  });
});

describe("state-dependent controls", () => {
  it("cannot prepare again or reject once sent", () => {
    for (const s of ["SUBMITTING", "SUBMITTED", "VERIFICATION_PENDING", "VERIFIED"]) { expect(canPrepareAgain({ state: s })).toBe(false); expect(canReject({ state: s })).toBe(false); }
    expect(canPrepareAgain(null)).toBe(true);
    expect(canPrepareAgain({ state: "READY_FOR_REVIEW" })).toBe(true);
  });
  it("manual handoff switch / answers / re-check visibility", () => {
    expect(canSwitchToHandoff({ state: "APPROVED" })).toBe(true);
    expect(canSwitchToHandoff({ state: "VERIFIED" })).toBe(false);
    expect(showAnswers({ state: "READY_FOR_REVIEW" }, [{}])).toBe(true);
    expect(showAnswers({ state: "READY_FOR_REVIEW" }, [])).toBe(false);
    expect(showAnswers({ state: "APPROVED" }, [{}])).toBe(false);
    expect(showRecheck({ state: "APPROVED" })).toBe(true);
    expect(showRecheck({ state: "SUBMITTED" })).toBe(false);
  });
});

describe("answers payload", () => {
  it("never sends empty, null or unticked answers", () => {
    expect(answersPayload({ a: "", b: null, c: undefined, d: false, e: "Yes", f: true })).toEqual({ e: "Yes", f: true });
  });
});

describe("queries and urls", () => {
  it("repeats the states parameter", () => {
    expect(applicationsPagePath(["A", "B"])).toBe("/api/applications/page?states=A&states=B&dismissed=false&limit=25");
    expect(TAB_STATES["Manual handoff"]).toEqual(["MANUAL_HANDOFF_REQUIRED"]);
  });
  it("only opens http(s) links", () => {
    expect(safeHttpUrl("https://example.com/apply")).toBe("https://example.com/apply");
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("data:text/html,x")).toBeNull();
    expect(safeHttpUrl("not a url")).toBeNull();
    expect(safeHttpUrl(null)).toBeNull();
  });
  it("confirm body drops empty text", () => {
    expect(confirmBody(true, "", "")).toEqual({ submitted: true, reference: null, notes: null });
    expect(confirmBody(false, "R1", "n")).toEqual({ submitted: false, reference: "R1", notes: "n" });
  });
});

describe("Needs your action tab and job order", () => {
  it("the first tab lists every state that waits for you, including manual handoff", () => {
    expect(OVERVIEW_TABS[0]).toBe("Needs your action");
    expect(TAB_STATES["Needs your action"]).toEqual(expect.arrayContaining(["READY_FOR_REVIEW", "AWAITING_USER_INPUT", "APPROVED", "MANUAL_HANDOFF_REQUIRED", "SUBMISSION_FAILED", "VERIFICATION_PENDING"]));
  });
  it("orders jobs by score (unscored last) and keeps the listed order for ties", () => {
    const jobs = [{ job_id: "a" }, { job_id: "b" }, { job_id: "c" }, { job_id: "d" }];
    const score: Record<string, number> = { a: 50, b: 74.5, d: 50 };
    expect(orderJobs(jobs, (id) => score[id]).map((j) => j.job_id)).toEqual(["b", "a", "d", "c"]);
  });
});
