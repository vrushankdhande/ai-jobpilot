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

describe("Naukri links from the clipboard's HTML copy", async () => {
  const { naukriLinksFromHtml, naukriLinksFor } = await import("./naukri");
  it("keeps only real naukri.com job links, without tracking parameters, and matches them to jobs by title", () => {
    const html = `<a href="https://www.naukri.com/job-listings-ai-engineer-acme-pune-5-to-10-years-211125012345?src=jobsearchDesk&sid=1">AI Engineer</a>
      <a href="/acme-analytics-jobs-careers-123">Acme Analytics</a><a href="https://naukri.com.evil.example/job-listings-x-211125099999">Data Scientist</a>
      <a href="/job-listings-data-scientist-beta-labs-mumbai-2-to-5-years-211125054321">Data Scientist</a>`;
    const links = naukriLinksFromHtml(html);
    expect(links).toEqual([
      { url: "https://www.naukri.com/job-listings-ai-engineer-acme-pune-5-to-10-years-211125012345", text: "AI Engineer" },
      { url: "https://www.naukri.com/job-listings-data-scientist-beta-labs-mumbai-2-to-5-years-211125054321", text: "Data Scientist" },
    ]);
    expect(naukriLinksFor([{ title: "Data Scientist" }, { title: "AI Engineer" }, { title: "ML Engineer" }], links))
      .toEqual([links[1].url, links[0].url, ""]);
  });
  it("falls back to page order when no link text matches but the counts agree, and gives nothing otherwise", () => {
    const links = [{ url: "https://www.naukri.com/job-listings-a-211125000001", text: "" }, { url: "https://www.naukri.com/job-listings-b-211125000002", text: "" }];
    expect(naukriLinksFor([{ title: "A" }, { title: "B" }], links)).toEqual([links[0].url, links[1].url]);
    expect(naukriLinksFor([{ title: "A" }], links)).toEqual([""]);
    expect(naukriLinksFromHtml("")).toEqual([]);
  });
});
