import { describe, expect, it } from "vitest";
import { addBody, badge, editBody, editFormOf, emptyAddForm, INTERVIEW_STATUS, listParams, tomorrow, utcParts } from "./interviews";

describe("add form", () => {
  it("defaults to tomorrow 10:00, Round 1, 60 min, 60 min reminder", () => {
    const f = emptyAddForm(new Date(2026, 10, 13, 15, 0));
    expect(f).toMatchObject({ date: "2026-11-14", time: "10:00", round: "Round 1", duration: 60, remind: 60, company: "", title: "", link: "" });
    expect(tomorrow(new Date(2026, 11, 31))).toBe("2027-01-01");
  });
  it("builds the request body; empty optional text becomes null; the typed wall-clock time is kept", () => {
    const f = { ...emptyAddForm(new Date(2026, 10, 13)), company: "Co", title: "T", round: "", link: "https://meet.example.com/x", linkedApp: "app1", notes: "" };
    expect(addBody(f)).toEqual({ company_name: "Co", job_title: "T", round: "Round 1", kind: "video", scheduled_at: "2026-11-14T10:00:00", duration_minutes: 60, meeting_link: "https://meet.example.com/x",
      interviewers: null, preparation_notes: null, reminder_minutes_before: 60, application_id: "app1" });
    expect("application_id" in addBody({ ...f, linkedApp: "" })).toBe(false);
  });
  it("passes a javascript: link to the server unchanged (the server must reject it, the UI must not silently fix it)", () => {
    expect(addBody({ ...emptyAddForm(), link: "javascript:alert(1)" }).meeting_link).toBe("javascript:alert(1)");
  });
});

describe("edit form", () => {
  const cur = { status: "scheduled", outcome: "pending", kind: "video", scheduled_at: "2026-11-15T04:30:00Z", meeting_link: null, preparation_notes: null, feedback: null };
  it("splits the stored UTC time (with or without a zone suffix)", () => {
    expect(utcParts("2026-11-15T04:30:00Z")).toEqual({ date: "2026-11-15", time: "04:30" });
    expect(utcParts("2026-11-15T04:30:00")).toEqual({ date: "2026-11-15", time: "04:30" });
    expect(utcParts("2026-11-15T10:00:00+05:30")).toEqual({ date: "2026-11-15", time: "04:30" });
  });
  it("sends scheduled_at only when the time was changed", () => {
    const o = editFormOf(cur);
    expect(editBody(o, o)).toEqual({ status: "scheduled", outcome: "pending", kind: "video", meeting_link: "", preparation_notes: "", feedback: "" });
    expect(editBody({ ...o, time: "05:00" }, o).scheduled_at).toBe("2026-11-15T05:00:00+00:00");
    expect(editBody({ ...o, date: "2026-11-16" }, o).scheduled_at).toBe("2026-11-16T04:30:00+00:00");
  });
});

describe("list filters and badges", () => {
  it("only sends the filters that are set", () => {
    expect(listParams({ q: "", status: "Any", outcome: "Any", newest: false })).toEqual({ newest_first: false });
    expect(listParams({ q: "acme", status: "completed", outcome: "offer", newest: true })).toEqual({ newest_first: true, q: "acme", status: "completed", outcome: "offer" });
  });
  it("falls back to the raw value, then a dash", () => {
    expect(badge(INTERVIEW_STATUS, "no_show")).toBe("⚠️ no show");
    expect(badge(INTERVIEW_STATUS, "weird")).toBe("weird");
    expect(badge(INTERVIEW_STATUS, null)).toBe("—");
  });
});
