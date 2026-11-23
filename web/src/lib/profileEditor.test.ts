import { describe, expect, it } from "vitest";
import {
  CERT_SPEC, EDUCATION_SPEC, EXPERIENCE_SPEC, PROJECT_SPEC, SEP, checkResumeFile, formToPayload, fromRows, linesToList, listToLines, parseYears, profileToForm, toRows,
} from "./profileEditor";

describe("toRows / fromRows", () => {
  it("round-trips list cells through the separator", () => {
    const rows = toRows([{ company_name: "Acme", responsibilities: ["Built X", "Ran Y"], is_current: true }], EXPERIENCE_SPEC);
    expect(rows[0].responsibilities).toBe(`Built X${SEP}Ran Y`);
    expect(rows[0].is_current).toBe(true);
    const back = fromRows(rows, EXPERIENCE_SPEC);
    expect(back[0].responsibilities).toEqual(["Built X", "Ran Y"]);
    expect(back[0].company_name).toBe("Acme");
    expect(back[0].job_title).toBeNull();                                  // blank stays blank, nothing is invented
  });
  it("drops fully empty rows but keeps a row with any content", () => {
    expect(fromRows([{ company_name: "", job_title: "  ", responsibilities: "", is_current: false }], EXPERIENCE_SPEC)).toEqual([]);
    expect(fromRows([{ project_name: "", technologies: "Py ;; SQL" }], PROJECT_SPEC)).toHaveLength(1);
  });
  it("a ticked checkbox alone is not content (matches the Streamlit page)", () => {
    expect(fromRows([{ is_current: true }], EXPERIENCE_SPEC)).toEqual([]);
  });
  it("ints: blank -> null, numbers parsed, garbage passed through for the API to reject", () => {
    const [r] = fromRows([{ degree: "BSc", start_year: "", graduation_year: "2019.0" }], EDUCATION_SPEC);
    expect(r.start_year).toBeNull();
    expect(r.graduation_year).toBe(2019);
    expect(fromRows([{ degree: "BSc", graduation_year: "soon" }], EDUCATION_SPEC)[0].graduation_year).toBe("soon");
    expect(fromRows([{ degree: "BSc", graduation_year: "0x10" }], EDUCATION_SPEC)[0].graduation_year).toBe("0x10");
  });
  it("trims strings and ignores empty list items", () => {
    expect(fromRows([{ certification_name: "  AWS  " }], CERT_SPEC)[0].certification_name).toBe("AWS");
    expect(fromRows([{ project_name: "p", achievements: " ;;  ;; a ;; " }], PROJECT_SPEC)[0].achievements).toEqual(["a"]);
  });
  it("toRows turns missing values into blanks, never undefined", () => {
    const [r] = toRows([{}], EDUCATION_SPEC);
    expect(r.start_year).toBe("");
    expect(r.degree).toBe("");
    expect(toRows(null, EDUCATION_SPEC)).toEqual([]);
  });
});

describe("lines and years", () => {
  it("splits on lines and commas and drops blanks", () => {
    expect(linesToList("Python, SQL\n\n  Go  \r\nRust")).toEqual(["Python", "SQL", "Go", "Rust"]);
    expect(linesToList(null)).toEqual([]);
    expect(listToLines(["a", "b"])).toBe("a\nb");
    expect(listToLines(null)).toBe("");
  });
  it("parseYears keeps unknown unknown", () => {
    expect(parseYears("")).toBeNull();
    expect(parseYears("  ")).toBeNull();
    expect(parseYears("3.5")).toBe(3.5);
    expect(parseYears("lots")).toBe("lots");
  });
});

describe("profile <-> form <-> payload", () => {
  const profile = {
    contact: { full_name: "Jane", email: null, phone: "1", location: null, linkedin_url: null, github_url: null, portfolio_url: null },
    summary: { professional_summary: "S", current_job_title: null, total_experience_years: null, primary_domains: ["NLP"], career_objective: null },
    technical_skills: { programming_languages: ["Python", "Go"] }, work_experience: [{ company_name: "Acme", responsibilities: ["x"] }], projects: [], education: [], certifications: [],
    achievements: ["Won"], review_status: "pending_review",
  };
  it("never fills in missing data", () => {
    const form = profileToForm(profile);
    expect(form.contact.email).toBe("");
    expect(form.years).toBe("");
    const out = formToPayload(form);
    expect(out.summary.total_experience_years).toBeNull();
    expect(out.review_status).toBe("pending_review");
    expect(out.technical_skills.programming_languages).toEqual(["Python", "Go"]);
    expect(out.technical_skills.databases).toEqual([]);
    expect(out.work_experience).toHaveLength(1);
    expect(out.achievements).toEqual(["Won"]);
  });
  it("reviewed flag controls review_status", () => {
    const form = { ...profileToForm(profile), reviewed: true };
    expect(formToPayload(form).review_status).toBe("reviewed");
  });
});

describe("checkResumeFile", () => {
  it("accepts pdf/docx only", () => {
    expect(checkResumeFile("cv.PDF", 10)).toBeNull();
    expect(checkResumeFile("cv.docx", 10)).toBeNull();
    expect(checkResumeFile("cv.exe", 10)).toMatch(/PDF or DOCX/);
    expect(checkResumeFile("cv.pdf.exe", 10)).toMatch(/PDF or DOCX/);
    expect(checkResumeFile("cv.pdf", 0)).toMatch(/empty/);
    expect(checkResumeFile("cv.pdf", 31 * 1024 * 1024)).toMatch(/too large/);
  });
});
