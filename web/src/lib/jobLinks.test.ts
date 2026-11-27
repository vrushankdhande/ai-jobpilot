import { describe, expect, it } from "vitest";
import { jobSiteLinks } from "./jobLinks";

describe("job site links", () => {
  it("gives each site's own recommendations page and a search for the title and city, https only", () => {
    const rows = jobSiteLinks("AI Engineer", "Pune");
    expect(rows.map((r) => r.site)).toEqual(["LinkedIn", "Naukri", "Indeed", "Instahyre"]);
    for (const r of rows) {
      expect(r.recommended).toMatch(/^https:\/\//);
      if (r.search) expect(r.search).toMatch(/^https:\/\//);
    }
    expect(rows[0].search).toContain("keywords=AI+Engineer");
    expect(rows[1].search).toContain("naukri.com");
    expect(rows[2].search).toBe("https://in.indeed.com/jobs?q=AI+Engineer&l=Pune&fromage=7&sort=date");
    expect(jobSiteLinks("", "")[2].search).toContain("q=AI+Engineer&l=Pune");          // sensible defaults
  });
});
