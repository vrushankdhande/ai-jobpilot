import { describe, expect, it } from "vitest";
import { naukriSearchUrl, parseNaukriJobUrl } from "./naukri";

describe("naukriSearchUrl", () => {
  it("returns null without keywords", () => {
    expect(naukriSearchUrl({ keywords: "" })).toBeNull();
    expect(naukriSearchUrl({ keywords: "   " })).toBeNull();
    expect(naukriSearchUrl({ keywords: "###" })).toBeNull();
  });

  it("builds a keywords-only link", () => {
    const u = new URL(naukriSearchUrl({ keywords: "AI engineer" })!);
    expect(u.origin).toBe("https://www.naukri.com");
    expect(u.pathname).toBe("/ai-engineer-jobs");
    expect(u.searchParams.get("k")).toBe("AI engineer");
    expect([...u.searchParams.keys()]).toEqual(["k"]);
  });

  it("adds location, experience and posted-within", () => {
    const u = new URL(naukriSearchUrl({ keywords: "Machine Learning Engineer", location: "Pune", experienceYears: 3, postedWithin: "week" })!);
    expect(u.pathname).toBe("/machine-learning-engineer-jobs-in-pune");
    expect(u.searchParams.get("k")).toBe("Machine Learning Engineer");
    expect(u.searchParams.get("l")).toBe("Pune");
    expect(u.searchParams.get("experience")).toBe("3");
    expect(u.searchParams.get("jobAge")).toBe("7");
  });

  it("ignores out-of-range experience and 'any' posted date", () => {
    for (const bad of [-1, 99, NaN, null]) {
      expect(new URL(naukriSearchUrl({ keywords: "data scientist", experienceYears: bad as number | null })!).searchParams.has("experience")).toBe(false);
    }
    expect(new URL(naukriSearchUrl({ keywords: "data scientist", postedWithin: "any" })!).searchParams.has("jobAge")).toBe(false);
  });

  it("keeps hostile text out of the path and encodes it in the query", () => {
    const url = naukriSearchUrl({ keywords: "ai/../../evil?x=1&y=2#frag", location: "Pune<script>" })!;
    const u = new URL(url);
    expect(u.hostname).toBe("www.naukri.com");
    expect(u.pathname).toMatch(/^\/[a-z0-9-]+$/);
    expect(u.hash).toBe("");
    expect(u.searchParams.get("k")).toBe("ai/../../evil?x=1&y=2#frag");
    expect(url).not.toContain("<script>");
  });
});

describe("parseNaukriJobUrl", () => {
  const ID = "170924012345";
  const link = `https://www.naukri.com/job-listings-ai-engineer-acme-analytics-pune-2-to-5-years-${ID}`;

  it("reads the id and drops tracking parameters", () => {
    expect(parseNaukriJobUrl(`${link}?src=jobsearchDesk&sid=SECRET&xp=1`)).toEqual({ jobId: ID, canonicalUrl: link });
    expect(parseNaukriJobUrl(`${link}/`)).toEqual({ jobId: ID, canonicalUrl: link });
    expect(parseNaukriJobUrl(`  ${link}  `)?.jobId).toBe(ID);
    expect(parseNaukriJobUrl(link.replace("www.", "m."))?.canonicalUrl).toBe(link);
  });

  it("rejects look-alike hosts, credentials, other schemes and non-job pages", () => {
    expect(parseNaukriJobUrl(link.replace("naukri.com", "naukri.com.evil.example"))).toBeNull();
    expect(parseNaukriJobUrl(link.replace("www.naukri.com", "evilnaukri.com"))).toBeNull();
    expect(parseNaukriJobUrl(link.replace("https://", "https://user:pw@"))).toBeNull();
    expect(parseNaukriJobUrl(link.replace("https:", "javascript:"))).toBeNull();
    expect(parseNaukriJobUrl("https://www.naukri.com/ai-engineer-jobs?k=ai")).toBeNull();
    expect(parseNaukriJobUrl("https://www.naukri.com/job-listings-short-123")).toBeNull();      // id too short
    expect(parseNaukriJobUrl("https://www.linkedin.com/jobs/view/4485396197/")).toBeNull();
    expect(parseNaukriJobUrl("not a url")).toBeNull();
    expect(parseNaukriJobUrl("")).toBeNull();
  });
});
