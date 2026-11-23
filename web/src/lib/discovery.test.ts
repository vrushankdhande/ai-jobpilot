import { buildDiscoverBody, buildImportBody, buildJobParams, bySource, defaultFilters, exp, importMessage, jobRows, latestScores, money, nextDayIso, runProgress, sourceRows, splitList } from "./discovery";

describe("discovery helpers", () => {
  it("splits lists on commas and newlines and drops blanks", () => {
    expect(splitList("a, b\n\n c ,")).toEqual(["a", "b", "c"]);
    expect(splitList("   ")).toEqual([]);
  });

  it("builds the discover body with null for zero filters", () => {
    const body = buildDiscoverBody({ usePrefs: true, titles: "ML Engineer", skills: "", locations: "Pune, Remote", companies: "", modes: ["remote"], employment: [], minSalary: 0, expMax: 0, postedDays: 7, sources: ["career_pages"] });
    expect(body).toEqual({ sources: ["career_pages"], use_preferences: true, titles: ["ML Engineer"], skills: [], locations: ["Pune", "Remote"], companies: [], work_modes: ["remote"], employment_types: [],
      min_salary: null, experience_max: null, posted_within_days: 7 });
  });

  it("builds list params only for filters that are set", () => {
    expect(buildJobParams(defaultFilters())).toEqual({ sort_by: "discovered_at", descending: true, preferred_only: "true" });   // fit-my-preferences is on by default
    expect(buildJobParams({ ...defaultFilters(), preferredOnly: false })).toEqual({ sort_by: "discovered_at", descending: true });
    const p = buildJobParams({ ...defaultFilters(), title: "x", source: "linkedin", maxExp: 3, minScore: 60, sortLabel: "Company", desc: false, from: "2026-11-05", to: "2026-11-30", reviewOnly: true });
    expect(p).toMatchObject({ title: "x", source: "linkedin", max_experience: 3, min_match_score: 60, sort_by: "company_name", descending: false, discovered_after: "2026-11-05T00:00:00", discovered_before: "2026-12-01T00:00:00", needs_review: true });
  });

  it("rolls the end date to the next day, across month and year ends", () => {
    expect(nextDayIso("2026-12-31")).toBe("2027-01-01T00:00:00");
    expect(nextDayIso("2028-02-28")).toBe("2028-02-29T00:00:00");
  });

  it("formats salary and experience without inventing values", () => {
    expect(money({ salary_min: null, salary_max: null })).toBe("—");
    expect(money({ salary_min: 1000000, salary_max: 2000000, currency: "INR" })).toBe("INR 1,000,000–2,000,000");
    expect(money({ salary_min: null, salary_max: 500000, currency: null })).toBe("500,000");
    expect(exp({ experience_min: null, experience_max: null })).toBe("—");
    expect(exp({ experience_min: 2, experience_max: 5.5 })).toBe("2–5.5 yrs");
    expect(exp({ experience_min: 3, experience_max: null })).toBe("3+ yrs");
  });

  it("builds source rows, marking unknown status and career-page configuration", () => {
    const rows = sourceRows([{ source: "career_pages", status: "ENABLED", automated_discovery: true, manual_import: false, companies_configured: 2 },
      { source: "linkedin", status: "MANUAL_ONLY", automated_discovery: false, manual_import: true, reason: "ToS", jobs_stored: 4 }, { source: "x", status: "WEIRD", automated_discovery: false, manual_import: false }]);
    expect(rows[0]).toMatchObject({ source: "Company career pages", status: "✅ ENABLED", automated: "yes", why: "2 company(ies) configured in config/career_sources.yaml" });
    expect(rows[1]).toMatchObject({ source: "LinkedIn", status: "✋ MANUAL_ONLY", automated: "no", stored: 4, why: "ToS" });
    expect(rows[2].status).toBe("❔ WEIRD");
  });

  it("keeps the newest score per job and counts jobs by source", () => {
    expect(latestScores([{ job_id: "a", match_score: 80 }, { job_id: "a", match_score: 10 }, { job_id: "b", match_score: 55 }])).toEqual({ a: 80, b: 55 });
    expect(latestScores({ detail: "err" })).toEqual({});
    expect(bySource([{ source: "x" }, { source: "x" }, { source: "y" }])).toEqual({ x: 2, y: 1 });
  });

  it("builds job rows with duplicate flags", () => {
    const r = jobRows([{ job_id: "1", job_title: null, company_name: "Co", location: ["Pune"], work_mode: null, experience_min: null, experience_max: null, salary_min: null, salary_max: null,
      posted_date: "2026-11-05T00:00:00", discovered_at: "2026-11-06T10:00:00", sources: [{ source: "b" }, { source: "a" }, { source: "a" }], status: "active", needs_review: true, dedup_status: "flagged" }], { "1": 71 });
    expect(r[0]).toMatchObject({ title: "(unknown)", match: 71, posted: "2026-11-05", discovered: "2026-11-06", sources: "a, b", duplicate: "⚠️ possible duplicate", mode: "—" });
  });

  it("sends only non-empty import fields and explains each outcome", () => {
    expect(buildImportBody("", { job_title: "T", company_name: "", location: "" })).toEqual({ url: null, job: { job_title: "T" } });
    expect(importMessage("flag")).toMatch(/possible duplicate/);
    expect(importMessage("other")).toBe("other");
  });

  it("caps run progress below 100% while running", () => {
    expect(runProgress({ requested_sources: ["a", "b"], sources: { a: { state: "done" }, b: { state: "running" } } })).toBeCloseTo(0.75);
    expect(runProgress({ requested_sources: ["a"], sources: { a: { state: "done" } } })).toBe(0.95);
  });
});
