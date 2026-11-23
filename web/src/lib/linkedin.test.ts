import { describe, expect, it } from "vitest";
import { linkedinSearchUrl, parseLinkedInJobUrl, suggestTitle } from "./linkedin";

describe("linkedinSearchUrl", () => {
  it("builds a search link from keywords, location, work modes and recency", () => {
    const url = linkedinSearchUrl({ keywords: "  AI   Engineer ", location: "Pune, India", workModes: ["remote", "hybrid"], postedWithin: "week", sortRecent: true })!;
    const u = new URL(url);
    expect(u.origin + u.pathname).toBe("https://www.linkedin.com/jobs/search/");
    expect(u.searchParams.get("keywords")).toBe("AI Engineer");
    expect(u.searchParams.get("location")).toBe("Pune, India");
    expect(u.searchParams.get("f_WT")).toBe("2,3");
    expect(u.searchParams.get("f_TPR")).toBe("r604800");
    expect(u.searchParams.get("sortBy")).toBe("DD");
  });
  it("returns null without keywords and omits empty filters", () => {
    expect(linkedinSearchUrl({ keywords: "   " })).toBeNull();
    const u = new URL(linkedinSearchUrl({ keywords: "data scientist" })!);
    expect([...u.searchParams.keys()]).toEqual(["keywords"]);
  });
  it("encodes hostile text so it cannot change the link's host or add parameters", () => {
    const url = linkedinSearchUrl({ keywords: "x&f_WT=9#evil", location: "a/../b?c=d" })!;
    const u = new URL(url);
    expect(u.hostname).toBe("www.linkedin.com");
    expect(u.searchParams.get("keywords")).toBe("x&f_WT=9#evil");
    expect(u.searchParams.has("f_WT")).toBe(false);
  });
  it("is capped in length", () => {
    expect(new URL(linkedinSearchUrl({ keywords: "a".repeat(500) })!).searchParams.get("keywords")).toHaveLength(120);
  });
});

describe("parseLinkedInJobUrl", () => {
  const want = { jobId: "4485396197", canonicalUrl: "https://www.linkedin.com/jobs/view/4485396197/" };
  it("reads the id from a search-results link (currentJobId)", () => {
    expect(parseLinkedInJobUrl("https://www.linkedin.com/jobs/search-results/?currentJobId=4485396197&keywords=ai%20engineer%20&origin=JOBS_HOME_KEYWORD_HISTORY&geoId=90009642")).toEqual(want);
  });
  it("reads the id from /jobs/view links, with or without a slug", () => {
    expect(parseLinkedInJobUrl("https://www.linkedin.com/jobs/view/4485396197/")).toEqual(want);
    expect(parseLinkedInJobUrl("https://in.linkedin.com/jobs/view/ai-engineer-at-acme-4485396197?trk=x")).toEqual(want);
    expect(parseLinkedInJobUrl("  https://linkedin.com/jobs/view/4485396197  ")).toEqual(want);
  });
  it("rejects look-alike hosts, credentials, other schemes and non-job links", () => {
    for (const bad of [
      "https://linkedin.com.evil.example/jobs/view/4485396197/", "https://evil.example/?currentJobId=4485396197", "https://www.linkedin.com@evil.example/jobs/view/4485396197/",
      "https://user:pw@www.linkedin.com/jobs/view/4485396197/", "javascript:alert(1)", "ftp://www.linkedin.com/jobs/view/4485396197/", "https://www.linkedin.com/in/someone/",
      "https://www.linkedin.com/jobs/view/123/", "https://www.linkedin.com/jobs/search/?currentJobId=abc", "not a url", "",
    ]) expect(parseLinkedInJobUrl(bad), bad).toBeNull();
  });
  it("always returns the clean canonical link, never the original query string", () => {
    expect(parseLinkedInJobUrl("https://www.linkedin.com/jobs/view/4485396197/?trackingId=SECRET&refId=zzz")!.canonicalUrl).not.toContain("SECRET");
  });
});

describe("suggestTitle", () => {
  it("suggests the first short line and nothing for sentences or empty text", () => {
    expect(suggestTitle("\n  AI Engineer \nAcme · Pune\nWe are hiring.")).toBe("AI Engineer");
    expect(suggestTitle("We are looking for an engineer to join our growing team.")).toBe("");
    expect(suggestTitle("")).toBe("");
    expect(suggestTitle("x".repeat(200))).toBe("");
  });
});
