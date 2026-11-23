import { describe, expect, it } from "vitest";
import { parseNaukriResults, postedToDate } from "./naukri";

const TODAY = new Date(2026, 10, 22);
// Shape of a Naukri results page after Ctrl+A / Ctrl+C (header, cards, footer). Written for the test; not copied from Naukri.
const PAGE = `Jobs
AI Engineer Jobs In Pune
1 - 20 of 312
Sort by: Relevance
AI Engineer
Acme Analytics
4.1
212 Reviews
5-10 Yrs
Not disclosed
Pune
Build and deploy LLM based applications; strong Python and PyTorch required.
python
machine learning
llm
1 Day Ago
Save
Senior Machine Learning Engineer
Beta Labs Private Limited
3-8 Yrs
12-20 Lacs PA
Pune, Bengaluru
Work on computer vision models in production.
computer vision, deep learning, aws
Just Now
Save
Hide
Lead Data Scientist
Gamma Tech
10-15 Yrs
Hybrid - Pune
Lead a team of data scientists.
30+ Days Ago
Save
Footer links
About us`;

describe("parseNaukriResults", () => {
  const jobs = parseNaukriResults(PAGE, TODAY);
  it("finds one entry per card and skips the page header and footer", () => {
    expect(jobs.map((j) => j.title)).toEqual(["AI Engineer", "Senior Machine Learning Engineer", "Lead Data Scientist"]);
    expect(jobs.map((j) => j.company)).toEqual(["Acme Analytics", "Beta Labs Private Limited", "Gamma Tech"]);   // ratings and review counts are not companies
  });
  it("reads experience, salary, location and posted date; unknown stays null", () => {
    expect(jobs[0]).toMatchObject({ experience: "5-10 Yrs", salary: "Not disclosed", location: "Pune", posted: "1 Day Ago", postedDate: "2026-11-21" });
    expect(jobs[1]).toMatchObject({ experience: "3-8 Yrs", salary: "12-20 Lacs PA", location: "Pune, Bengaluru", postedDate: "2026-11-22" });
    expect(jobs[2]).toMatchObject({ salary: null, location: "Hybrid - Pune", posted: "30+ Days Ago", postedDate: null });
  });
  it("keeps the snippet and skills as the job text (for scoring) and never mixes cards", () => {
    expect(jobs[0].text).toContain("PyTorch"); expect(jobs[0].text).toContain("Experience: 5-10 Yrs"); expect(jobs[0].text).not.toContain("computer vision");
    expect(jobs[1].text).toContain("computer vision"); expect(jobs[1].text).not.toMatch(/\bSave\b|\bHide\b/);
  });
  it("returns nothing for text that is not a results page", () => {
    expect(parseNaukriResults("hello world\nnothing here")).toEqual([]);
  });
});

describe("postedToDate", () => {
  it("counts back from today and leaves open-ended ages unknown", () => {
    expect(postedToDate("Today", TODAY)).toBe("2026-11-22");
    expect(postedToDate("3 Days Ago", TODAY)).toBe("2026-11-19");
    expect(postedToDate("30+ Days Ago", TODAY)).toBeNull();
    expect(postedToDate(null, TODAY)).toBeNull();
  });
});
