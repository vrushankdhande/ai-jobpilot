import { describe, expect, it } from "vitest";
import { ageInDays, attachLinks, jobLinksFromHtml, linkedinJobUrl, parseLinkedInResults, undouble } from "./linkedinPaste";

// The jobs part is a real LinkedIn search page copied with Ctrl+A / Ctrl+C (Nov 2026, "Artificial Intelligence Engineer remote", Pune).
// The messaging overlay that came with it is replaced by made-up text: it must be ignored either way.
const CARDS: Array<[string, string, string, string[]]> = [
  ["Selected, AI Research Engineer (Remote)AI Research Engineer (Remote)", "Joblet-AI", "India (Remote)", ["Posted 10 minutes ago10 minutes ago", "·", "Be an early applicant"]],
  ["Senior ML EngineerSenior ML Engineer", "Avoma", "Pune Division (Remote)", ["Posted 1 year ago1 year ago"]],
  ["AI Machine Learning Engineer - Remote - India (Verified job)AI Machine Learning Engineer - Remote - India ", "FullStack", "India (Remote)", ["Posted 1 week ago1 week ago"]],
  ["AI Solutions Engineer (Verified job)AI Solutions Engineer ", "Deltek", "India (Remote)", ["Posted 2 weeks ago2 weeks ago"]],
  ["AI Data Scientist (Verified job)AI Data Scientist ", "Deltek", "India (Remote)", ["Posted 2 weeks ago2 weeks ago"]],
  ["Senior AI Engineer - Chat & Agent SystemsSenior AI Engineer - Chat & Agent Systems", "Kantiv", "India (Remote)", ["Posted 3 weeks ago3 weeks ago"]],
  ["Data Scientist IIData Scientist II", "MRSOOL | مرسول", "India (Remote)", ["Posted 2 months ago2 months ago", "·", "Easy Apply"]],
  ["AI Engineer", "Jeeves", "India (Remote)", ["Posted 2 months ago2 months ago"]],
  ["Senior Gen AI Engineer", "Trinamix Inc", "Pune Division (Remote)", ["1 connection works here", "Posted 4 months ago4 months ago"]],
  ["Senior AI Engineer ", "H2O.ai", "India (Remote)", ["Posted 2 weeks ago2 weeks ago", "·", "Easy Apply"]],
  ["AI Engineer ", "Keywords Studios", "Pune Division (Remote)", ["Applied", "·", "Easy Apply"]],
  ["AI / Agent Engineer", "Netrix Global", "Pune Division", ["1 school alumni works here", "Posted 3 days ago3 days ago"]],
  ["AI Solutions Engineer", "Sleek", "India (Remote)", ["Posted 2 months ago2 months ago", "·", "Easy Apply"]],
  ["Data Scientist ML/Gen AI Engineer", "Irth", "India (Remote)", ["$45K/yr - $45K/yr", "Posted 1 month ago1 month ago"]],
  ["Backend Security Engineer (Remote)", "Joblet-AI", "India (Remote)", ["Posted 47 minutes ago47 minutes ago", "·", "Be an early applicant"]],
  ["AI Engineer", "Papigen", "India (Remote)", ["Actively reviewing applicants", "Posted 1 month ago1 month ago", "·", "Easy Apply"]],
  ["Senior AI Engineer", "Velsera", "India (Remote)", ["Posted 1 month ago1 month ago", "·", "Easy Apply"]],
  ["Lead ML Engineer – Classical ML & GenAI/RAG", "Delaplex", "India (Remote)", ["Actively reviewing applicants", "Applied", "·", "Easy Apply"]],
  ["Forward Deployed Engineer (TypeScript + AI) - Remote - India ", "FullStack", "India (Remote)", ["Posted 1 week ago1 week ago"]],
  ["Staff AI Engineer ", "G-P", "India (Remote)", ["Actively reviewing applicants", "Posted 2 months ago2 months ago", "·", "Easy Apply"]],
  ["AI Lead", "Papigen", "India (Remote)", ["Posted 1 month ago1 month ago", "·", "Easy Apply"]],
  ["Senior Data Scientist", "Uplers", "India (Remote)", ["1 connection works here", "Viewed"]],
  ["AI Engineer - L4", "Korza", "India (Remote)", ["Posted 1 month ago1 month ago"]],
  ["AI/ML Architect", "NewRocket", "Pune Division (Remote)", ["Applied", "·", "Easy Apply"]],
  ["Lead/Staff Engineer - Applied AI", "Jobgether", "India (Remote)", ["Posted 4 days ago4 days ago", "·", "Easy Apply"]],
];

const HEADER = "0 notifications\n\nArtificial Intelligence Engineer remote\n\n\n\n\nHome\nMy Network\nJobs\nMessaging\n13\nNotifications\n\n\nMe\n\nFor Business\nAdvertise\n\nJobs\n\nRemote\n\n" +
  "Date posted\n\nCompany\n\nEasy Apply\n\nExperience level\n\nEmployment type\n\nGen AI\n\nLLM\n\nAll filters\nReset\n99+ results\n\nPune District\n\nHow promoted jobs are ranked\n\n · \n\nHow AI search works\n\n\n";
const FOOTER = "\nAre these results helpful?\nYour feedback helps us improve search results.\n\n\n\n1\n\n2\n\n3\n\nNext\nAbout\n\nAccessibility\n\nHelp Center\n\nLinkedIn Corporation © 2026\n\n" +
  "Get job alerts for this search\n\nCompany logo for, Joblet-AI.\nJoblet-AI\n\n\nAI Research Engineer (Remote)\n\nIndia · 10 minutes ago · 3 people clicked apply\n\nRemote\nFull-time\nApply\n\nSave\n" +
  "About the job\nRole Title: AI Research Engineer (Remote)\nRole Type: Full-time\nLocation: Remote\n\nWhat We're Looking For1. Strong software engineering background with expertise in Python, C++.2. 3+ years of experience in software engineering, machine learning, AI research.\n" +
  "Apply Now!… more\nBenefits found in job post\n\n401(k)\n\nAbout the company\n\nJoblet-AI\n\n122,029 followers\n" +
  // made-up stand-in for the messaging overlay (names, conversations): must never become a job or part of a description
  "Alex SampleStatus is online\nMessagingYou are on the messaging overlay.\nJordan Example\n9:48 AM\nYou: Are there any openings for an AI Engineer role?\nSam Placeholder\nNov 22\nSam: Hiring is frozen in my office.\n";

const PAGE = HEADER + CARDS.map(([t, c, l, meta]) => [t, c, l, ...meta].join("\n\n")).join("\n\n\n") + FOOTER;
const TODAY = new Date(2026, 10, 24);

describe("parseLinkedInResults (real copied page)", () => {
  const jobs = parseLinkedInResults(PAGE, TODAY);

  it("finds every job card and nothing from the menus, filters or messages", () => {
    expect(jobs).toHaveLength(25);
    expect(jobs.map((j) => j.title)).not.toContain("Pune District");
    expect(jobs.some((j) => /Jordan|Sam|Alex|openings|Hiring is frozen/.test(j.title + j.company))).toBe(false);
  });

  it("cleans titles, splits location / work mode and reads the posted age", () => {
    expect(jobs[0]).toMatchObject({ title: "AI Research Engineer (Remote)", company: "Joblet-AI", location: "India", workMode: "remote", posted: "10 minutes ago", ageDays: 0, selected: true });
    expect(jobs[2].title).toBe("AI Machine Learning Engineer - Remote - India");
    expect(jobs[3].title).toBe("AI Solutions Engineer");
    expect(jobs[1]).toMatchObject({ title: "Senior ML Engineer", location: "Pune", workMode: "remote", ageDays: 365 });
    expect(jobs[11]).toMatchObject({ title: "AI / Agent Engineer", location: "Pune", workMode: null, ageDays: 3, postedDate: "2026-11-21" });
    expect(jobs[13]).toMatchObject({ company: "Irth", salary: "$45K/yr - $45K/yr", ageDays: 30 });
    expect(jobs[6].company).toBe("MRSOOL | مرسول");
  });

  it("marks the jobs LinkedIn shows as Applied, and leaves unknown dates unknown", () => {
    expect(jobs.filter((j) => j.applied).map((j) => j.company)).toEqual(["Keywords Studios", "Delaplex", "NewRocket"]);
    expect(jobs[10]).toMatchObject({ posted: null, postedDate: null, ageDays: null });
    expect(jobs[21]).toMatchObject({ company: "Uplers", applied: false, posted: null });
  });

  it("takes the open job's description from 'About the job' and stops before LinkedIn's own sections", () => {
    expect(jobs[0].description).toContain("3+ years of experience");
    expect(jobs[0].description).toContain("Apply Now!");
    expect(jobs[0].description).not.toMatch(/Benefits found|followers|Jordan|Hiring is frozen/);
    expect(jobs.filter((j) => j.description)).toHaveLength(1);
  });

  it("returns nothing for text that is not a results page", () => {
    expect(parseLinkedInResults("hello\nworld")).toEqual([]);
  });
});

describe("links", () => {
  it("reads /jobs/view/<id> links from the clipboard's HTML copy and matches them to jobs by title", () => {
    const html = `<ul><li><a href="/jobs/view/4300000001/?eBP=x&trk=abc">AI Research Engineer (Remote)<span>with verification</span></a><a href="/company/joblet-ai/">Joblet-AI</a></li>
      <li><a href="https://www.linkedin.com/jobs/view/4300000002/?refId=y">Senior ML Engineer</a></li><li><a href="/jobs/view/4300000002/">Senior ML Engineer</a></li></ul>`;
    const links = jobLinksFromHtml(html);
    expect(links.map((l) => l.id)).toEqual(["4300000001", "4300000002", "4300000002"]);
    const jobs = attachLinks(parseLinkedInResults(PAGE, TODAY), links);
    expect(jobs[0].jobId).toBe("4300000001");
    expect(jobs[1].jobId).toBe("4300000002");
    expect(jobs[2].jobId).toBeNull();
    expect(linkedinJobUrl(jobs[0].jobId)).toBe("https://www.linkedin.com/jobs/view/4300000001/");
  });

  it("falls back to the page address (currentJobId) for the open job, and ignores non-LinkedIn links", () => {
    expect(jobLinksFromHtml(`<a href="https://evil.example/jobs/view/4300000009/">x</a>`)).toEqual([]);
    const jobs = attachLinks(parseLinkedInResults(PAGE, TODAY), [], "https://www.linkedin.com/jobs/search/?currentJobId=4311112222&keywords=ai");
    expect(jobs[0].jobId).toBe("4311112222");
    expect(jobs.filter((j) => j.jobId)).toHaveLength(1);
  });

  it("undoubles screen-reader repeats and reads ages", () => {
    expect(undouble("AI EngineerAI Engineer")).toBe("AI Engineer");
    expect(undouble("Data Scientist II")).toBe("Data Scientist II");
    expect(ageInDays("2 months ago")).toBe(60);
    expect(ageInDays("Viewed")).toBeNull();
  });
});
