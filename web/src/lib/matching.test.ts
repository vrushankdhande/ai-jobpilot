import { describe, expect, it } from "vitest";
import { NO_FILTERS, categoryProgress, clampScore, completeness, expLabel, rangeProblem, roleFitLabel, scoreLabel, decodeJobDescription, eligLabel, g, historyParams, recommendationKind, scoreBody } from "./matching";
import { monthName, renderLines, resumeLines } from "./resumeText";

describe("matching helpers", () => {
  it("UNKNOWN eligibility is never shown as PASS", () => {
    expect(eligLabel("UNKNOWN")).toBe("❓ UNKNOWN");
    expect(eligLabel("PASS")).toBe("✅ PASS");
    expect(eligLabel("SOMETHING_NEW")).toBe("• SOMETHING_NEW");
    expect(eligLabel("SOMETHING_NEW")).not.toContain("✅");
  });
  it("an unevaluated category is UNKNOWN - not scored, not 0 points", () => {
    const p = categoryProgress({ status: "UNKNOWN", weight: 15, notes: ["no education in the JD"] });
    expect(p.text).toContain("UNKNOWN - not scored (weight 15)");
    expect(p.text).not.toContain("0.0 /");
    expect(categoryProgress({ status: "EVALUATED", ratio: 0.5, earned_points: 7.25, weight: 15 })).toEqual({ ratio: 0.5, text: "7.3 / 15 points" });
  });
  it("formats numbers like Python's :g", () => {
    expect(g(62.7)).toBe("62.7");
    expect(g(80)).toBe("80");
    expect(g(15.0)).toBe("15");
  });
  it("completeness is the share of the 18 key fields filled", () => {
    expect(completeness(0)).toBe(1);
    expect(completeness(9)).toBe(0.5);
    expect(completeness(40)).toBe(0);
  });
  it("recommendation severity", () => {
    expect(recommendationKind("RECOMMENDED")).toBe("success");
    expect(recommendationKind("NOT_ELIGIBLE")).toBe("error");
    expect(recommendationKind("ELIGIBILITY_UNRESOLVED")).toBe("warning");
  });
  it("history query only contains active filters and asks for one extra row", () => {
    expect(historyParams(NO_FILTERS, 1)).toEqual({ limit: 16, skip: 0, latest_only: "true", preferred_only: "true", sort: "score" });          // newest match per job, best first
    expect(historyParams({ ...NO_FILTERS, minScore: 60, eligibility: "PASS", recommendation: "RECOMMENDED", title: "ml", company: "acme", dateFrom: "2026-01-02", dateTo: "2026-01-03" }, 3)).toEqual({
      limit: 16, skip: 30, recommendation_code: "RECOMMENDED", min_score: 60, eligibility: "PASS", job_title: "ml", company: "acme", date_from: "2026-01-02T00:00:00", date_to: "2026-01-03T23:59:59",
      latest_only: "true", preferred_only: "true", sort: "score",
    });
    expect(historyParams({ ...NO_FILTERS, maxScore: 80, maxExperience: 5, latestOnly: false, sort: "date" }, 1)).toEqual({ limit: 16, skip: 0, max_score: 80, max_experience_required: 5, preferred_only: "true", sort: "date" });
  });
  it("score, experience and role-fit labels", () => {
    expect(scoreLabel(71.3)).toBe("71.3%");
    expect(scoreLabel(80)).toBe("80%");
    expect(expLabel(5)).toBe("5+ yrs");
    expect(expLabel(null)).toBe("-");
    expect(expLabel(undefined)).toBe("-");
    expect(roleFitLabel("different")).toContain("different");
    expect(roleFitLabel(null)).toBe("-");
  });
  it("score boxes stay within 0-100 and an inverted range is explained, not rewritten", () => {
    expect(clampScore(-5)).toBe(0);
    expect(clampScore(140)).toBe(100);
    expect(clampScore(Number.NaN)).toBe(0);
    expect(clampScore(71.6)).toBe(72);
    expect(rangeProblem(40, 80)).toBeNull();
    expect(rangeProblem(90, 80)).toMatch(/higher than Max score/);
  });
  it("Claude is only requested when the backend says it is available", () => {
    expect(scoreBody({}, false, true, false).use_ai).toBe(false);
    expect(scoreBody({}, false, true, true).use_ai).toBe(true);
    expect(scoreBody({}, true, false, true).allow_unreviewed).toBe(true);
  });
  it("job description file: UTF-8 only, BOM stripped, capped", () => {
    const enc = (s: string) => new TextEncoder().encode(s).buffer as ArrayBuffer;
    expect(decodeJobDescription(enc("﻿hello"))).toBe("hello");
    expect(decodeJobDescription(enc("a".repeat(60000)))).toHaveLength(50000);
    expect(() => decodeJobDescription(new Uint8Array([0xff, 0xfe, 0xfd]).buffer)).toThrow();
  });
});

describe("resume text preview", () => {
  it("never invents a month", () => {
    expect(monthName("2022-01")).toBe("Jan 2022");
    expect(monthName("2019")).toBe("2019");
    expect(monthName("2022-13")).toBe("2022-13");
    expect(monthName(null)).toBeNull();
  });
  it("renders the same plain text as the exports", () => {
    const lines = resumeLines({
      header: { full_name: "Jane", email: "j@x.com", phone: null }, summary: "Sum", skills: [{ label: "Languages", items: ["Python", "Go"] }],
      experience: [{ job_title: "Eng", company_name: "Acme", start_date: "2020-03", end_date: null, is_current: true, bullets: [{ text: "Did a thing" }] }],
      projects: [], education: [{ degree: "BSc", institution: "MIT", graduation_year: 2019 }], certifications: [], achievements: [{ text: "Won" }],
    });
    expect(lines).toContain("# Jane");
    expect(lines).toContain("Languages: Python, Go");
    expect(lines).toContain("Eng - Acme (Mar 2020 - Present)");
    expect(lines).toContain("- Did a thing");
    expect(lines).toContain("BSc - MIT - 2019");
    expect(lines).toContain("- Won");
  });
  it("covers the other document types and unknown types", () => {
    expect(renderLines("screening_answers", { answers: [{ question: "Q1", status: "answered", answer: "A1" }] })).toEqual(["Q: Q1", "A [answered]: A1", ""]);
    expect(renderLines("cover_letter", { salutation: "Hi", paragraphs: [{ text: "P" }], closing: "Bye", signature: null })).toEqual(["Hi", "", "P", "", "Bye", ""]);
    expect(renderLines("nope", {})).toEqual([]);
  });
});
