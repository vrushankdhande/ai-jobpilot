import { describe, expect, it } from "vitest";
import { applyLetterEdits, applyResumeEdits, applyScreeningEdits, approvalState, bulletsText, candidateInputs, issueIcon, lines, statusBadge } from "./documents";

const resume = () => ({
  summary: "Seasoned ML engineer.", summary_sources: ["profile.summary"],
  skills: [{ label: "Core", items: ["Python", "SQL"] }, { label: "Cloud", items: ["AWS"] }],
  experience: [{ job_title: "ML Engineer", company_name: "Acme", bullets: [{ text: "Built models", source: "exp.0.b0" }, { text: "Led team", source: "exp.0.b1" }] }],
  projects: [{ project_name: "P1", bullets: [{ text: "Did a thing", source: "proj.0.b0" }] }],
  achievements: [{ text: "Won award", source: "ach.0" }],
});

describe("documents lib", () => {
  it("lines trims and drops blanks", () => { expect(lines("  a \r\n\n b\n")).toEqual(["a", "b"]); expect(lines(null)).toEqual([]); });
  it("bulletsText joins bullet texts", () => { expect(bulletsText(resume().experience[0].bullets)).toBe("Built models\nLed team"); });

  it("unchanged resume bullets keep their evidence; new ones have no source", () => {
    const r = resume();
    const out = applyResumeEdits(r, { summary: r.summary, skills: { 0: "Python\nSQL", 1: "AWS" }, experience: { 0: "Built models\nBrand new line" }, projects: { 0: "Did a thing" }, achievements: "Won award" });
    expect(out.experience[0].bullets).toEqual([{ text: "Built models", source: "exp.0.b0" }, { text: "Brand new line", source: null }]);
    expect(out.summary_sources).toEqual(["profile.summary"]);
    expect(out.achievements[0].source).toBe("ach.0");
  });
  it("an edited summary loses its sources; an emptied skill group is removed; the input is not mutated", () => {
    const r = resume();
    const out = applyResumeEdits(r, { summary: "Different", skills: { 0: "Python", 1: "" }, experience: { 0: "x" }, projects: { 0: "" }, achievements: "" });
    expect(out.summary_sources).toEqual([]);
    expect(out.skills).toHaveLength(1);
    expect(out.projects[0].bullets).toEqual([]);
    expect(r.skills).toHaveLength(2);
    expect(r.experience[0].bullets).toHaveLength(2);
  });

  it("cover letter: keeps evidence for unchanged paragraphs, drops empty ones, signature blank -> null", () => {
    const content = { paragraphs: [{ text: "One", sources: ["a"] }, { text: "Two", sources: ["b"] }], signature: "Jane" };
    const out = applyLetterEdits(content, ["One", "  ", "Three"], "  ");
    expect(out.paragraphs).toEqual([{ text: "One", sources: ["a"] }, { text: "Three", sources: [] }]);
    expect(out.signature).toBeNull();
    expect(applyLetterEdits(content, ["One"]).signature).toBe("Jane");
  });

  it("screening: a written needs_input answer becomes 'answered'; an empty or placeholder one stays needs_input; unchanged is untouched", () => {
    const content = { answers: [
      { question: "Notice?", answer: "[CANDIDATE INPUT REQUIRED: notice]", status: "needs_input", sources: [], note: "n" },
      { question: "Why?", answer: "Because.", status: "answered", sources: ["profile"], note: null },
      { question: "Salary?", answer: "[CANDIDATE INPUT REQUIRED: salary]", status: "needs_input", sources: [], note: "s" },
    ] };
    const out = applyScreeningEdits(content, ["30 days", "Because.", ""]);
    expect(out.answers[0]).toMatchObject({ answer: "30 days", status: "answered", sources: ["candidate_input"], note: null });
    expect(out.answers[1]).toMatchObject({ status: "answered", sources: ["profile"] });
    expect(out.answers[2]).toMatchObject({ answer: "", status: "needs_input" });
    const again = applyScreeningEdits(content, ["[CANDIDATE INPUT REQUIRED: x]", "Because.", "[CANDIDATE INPUT REQUIRED: salary]"]);
    expect(again.answers[0].status).toBe("needs_input");
  });

  it("approval: failed blocks; warnings need acknowledgement; approved cannot be approved again", () => {
    const doc = (review: string, val: string) => ({ review_status: review, validation: { status: val } });
    expect(approvalState(doc("draft", "passed"), false).canApprove).toBe(true);
    expect(approvalState(doc("draft", "failed"), true)).toMatchObject({ blocked: true, canApprove: false });
    expect(approvalState(doc("draft", "passed_with_warnings"), false)).toMatchObject({ needsAck: true, canApprove: false });
    expect(approvalState(doc("draft", "passed_with_warnings"), true).canApprove).toBe(true);
    expect(approvalState(doc("approved", "passed"), false)).toMatchObject({ approved: true, canApprove: false });
  });

  it("candidate inputs drop blanks (never guessed)", () => { expect(candidateInputs({ a: " ", b: "yes", c: "" })).toEqual({ b: "yes" }); });
  it("badges and icons", () => { expect(statusBadge("failed")).toContain("failed"); expect(statusBadge("zzz")).toBe("zzz"); expect(issueIcon("error")).toBe("🛑"); expect(issueIcon("?")).toBe("•"); });
});
