import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FlashProvider } from "../components/ui";
import JobMatching from "./JobMatching";

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response> | undefined;
const isGet = (init?: RequestInit) => !init?.method || init.method === "GET";
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
function mockFetch(...handlers: Handler[]) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    for (const h of handlers) { const r = h(String(url), init); if (r) return r; }
    return json({ detail: `unmocked ${init?.method ?? "GET"} ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
const view = () => render(<MemoryRouter><FlashProvider><JobMatching /></FlashProvider></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());
window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;      // jsdom does not implement it

const cfg = { scoring_version: "1.2", recommendation_threshold: 60, ai: { enabled: false, reason: "no API key" } };
const resume = { resume_id: "r1", original_filename: "jane_cv.pdf", is_active: true };
const profile = (review = "reviewed") => ({ review_status: review, profile_version: 3, missing_fields: ["a", "b", "c"] });
const report = {
  match_id: "m1", match_score: 62.7, classification: "Moderate Match", data_coverage: 80, recommendation_code: "ELIGIBILITY_UNRESOLVED", recommendation: "Eligibility could not be fully checked.",
  jd_parser: "deterministic", scoring_version: "1.2", config_hash: "abc123", explanation: "Explained.", ai_summary: null, strengths: ["Python"], weaknesses: [], rejection_reasons: [],
  eligibility: { status: "UNKNOWN", checks: [{ name: "work_authorization", status: "UNKNOWN", hard: true, requirement: "Citizen", candidate_value: null, detail: "Not in profile" }] },
  category_details: [
    { name: "technical_skills", status: "EVALUATED", ratio: 0.6, earned_points: 15, weight: 25, notes: ["3 of 5"], evidence: [{ jd: "Python", candidate: "Python" }] },
    { name: "education", status: "UNKNOWN", ratio: 0, earned_points: 0, weight: 10, notes: ["JD names no degree"], evidence: [] },
  ],
  matched_skills: ["Python"], missing_mandatory_skills: ["Kubernetes"], missing_preferred_skills: [], partial_matches: [], irrelevant_candidate_skills: [],
  experience_analysis: [], responsibility_analysis: [], education_analysis: [], location_analysis: [], project_relevance: [], limitations: ["Rule based"],
};
const base = (review = "reviewed"): Handler => (url, init) => {
  if (url === "/api/matching/thresholds") return json(cfg);
  if (url === "/api/resumes") return json([resume]);
  if (url === "/api/resumes/r1/profile") return json(profile(review));
  if (url.startsWith("/api/matching/results") && isGet(init)) return json([]);
  return undefined;
};

describe("JobMatching", () => {
  it("shows a loading state", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    view();
    expect(screen.getAllByRole("progressbar").length).toBeGreaterThan(0);
  });

  it("shows an error when the backend is down", async () => {
    mockFetch((url) => (url === "/api/matching/thresholds" ? json({ detail: "Database unavailable" }, 503) : undefined));
    view();
    expect(await screen.findByText(/Database unavailable \(HTTP 503\)/)).toBeInTheDocument();
  });

  it("asks for an active resume when there is none", async () => {
    mockFetch((url) => (url === "/api/matching/thresholds" ? json(cfg) : url === "/api/resumes" ? json([{ ...resume, is_active: false }]) : undefined));
    view();
    expect(await screen.findByText(/No active resume/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Analyze/ })).not.toBeInTheDocument();
  });

  it("shows the profile summary, deterministic mode and an empty history", async () => {
    mockFetch(base());
    view();
    expect(await screen.findByText("A. Candidate profile")).toBeInTheDocument();
    expect(screen.getByText("83%")).toBeInTheDocument();                       // 1 - 3/18
    expect(screen.getByText(/Deterministic mode \(no API key\)/)).toBeInTheDocument();
    expect(await screen.findByText(/No saved matches for these filters\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Analyze & match/ })).toBeEnabled();
    expect(screen.getByLabelText(/Use Claude to refine/)).toBeDisabled();       // Claude is opt-in and unavailable here
  });

  it("an unreviewed profile cannot be matched until the user confirms", async () => {
    mockFetch(base("pending_review"));
    const user = userEvent.setup();
    view();
    const run = await screen.findByRole("button", { name: /Analyze & match/ });
    expect(run).toBeDisabled();
    await user.click(screen.getByLabelText(/match against the unreviewed profile/));
    expect(run).toBeEnabled();
  });

  it("runs a match and shows score and eligibility separately; UNKNOWN is not PASS and not 0", async () => {
    const fn = mockFetch((url, init) => (url === "/api/matching/score" && init?.method === "POST" ? json({ created: true, report }) : undefined), base());
    const user = userEvent.setup();
    view();
    await user.type(await screen.findByLabelText("Paste the job description"), "We need Python and Kubernetes.");
    await user.type(screen.getByLabelText("Job title"), "ML Engineer");
    await user.click(screen.getByRole("button", { name: /Analyze & match/ }));
    expect(await screen.findByText("C. Match score")).toBeInTheDocument();
    expect(screen.getByText("62.7 / 100")).toBeInTheDocument();
    expect(screen.getAllByText("❓ UNKNOWN").length).toBeGreaterThan(0);
    expect(screen.queryByText(/✅ PASS/)).not.toBeInTheDocument();
    expect(screen.getByText(/UNKNOWN - not scored \(weight 10\)\. JD names no degree/)).toBeInTheDocument();
    expect(screen.getByText(/a high score never overrides a failed mandatory requirement/)).toBeInTheDocument();
    expect(screen.getByText("Kubernetes")).toBeInTheDocument();
    const post = fn.mock.calls.find(([u]) => u === "/api/matching/score")!;
    const body = JSON.parse((post[1] as RequestInit).body as string);
    expect(body.job.job_title).toBe("ML Engineer");
    expect(body.use_ai).toBe(false);
    expect(body.allow_unreviewed).toBe(false);
  });

  it("says so when identical inputs were already scored", async () => {
    mockFetch((url, init) => (url === "/api/matching/score" && init?.method === "POST" ? json({ created: false, report }) : undefined), base());
    const user = userEvent.setup();
    view();
    await user.type(await screen.findByLabelText("Paste the job description"), "Same text");
    await user.click(screen.getByRole("button", { name: /Analyze & match/ }));
    expect(await screen.findByText(/Identical inputs were already scored/)).toBeInTheDocument();
  });

  it("shows the API error when scoring is rejected", async () => {
    mockFetch((url, init) => (url === "/api/matching/score" && init?.method === "POST" ? json({ detail: "job_description: too short" }, 422) : undefined), base());
    const user = userEvent.setup();
    view();
    await user.click(await screen.findByRole("button", { name: /Analyze & match/ }));
    expect(await screen.findByText(/job_description: too short \(HTTP 422\)/)).toBeInTheDocument();
    expect(screen.queryByText("C. Match score")).not.toBeInTheDocument();
  });

  it("lists saved matches and opens one", async () => {
    const row = { match_id: "m1", job_id: "j1", created_at: "2026-11-05T08:00:00", job_title: "ML Engineer", company_name: "Acme", match_score: 71, classification: "Good Match", eligibility_status: "PASS",
      recommendation_code: "RECOMMENDED", data_coverage: 90, profile_version: 3, scoring_version: "1.2", min_experience_required: 5, role_fit: "same", source: "Career page (greenhouse)", job_url: "https://boards.greenhouse.io/acme/jobs/1" };
    const fn = mockFetch((url, init) => (url.startsWith("/api/matching/results") && isGet(init) ? json([row]) : url === "/api/matching/m1" ? json(report) : undefined), base());
    const user = userEvent.setup();
    view();
    await screen.findByText("G. Match results");
    expect(await screen.findByText("✅ PASS")).toBeInTheDocument();
    expect(screen.getByText("71%")).toBeInTheDocument();                                           // the dedicated Score column
    expect(screen.getByText("5+ yrs")).toBeInTheDocument();                                        // experience required
    expect(screen.getByText("✅ same function")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Source" })).toBeInTheDocument();
    expect(screen.getByText("Career page (greenhouse)")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Open job/ });
    expect(link).toHaveAttribute("href", "https://boards.greenhouse.io/acme/jobs/1");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    await user.click(screen.getByRole("button", { name: "Open selected match" }));
    await waitFor(() => expect(screen.getByText("C. Match score")).toBeInTheDocument());
    expect(fn.mock.calls.some(([u]) => u === "/api/matching/m1")).toBe(true);
  });

  it("filters are sent to the API and a failed history load shows an error", async () => {
    const fn = mockFetch((url, init) => (url.startsWith("/api/matching/results") && isGet(init) && url.includes("job_title=zzz") ? json({ detail: "Database unavailable" }, 503) : undefined), base());
    const user = userEvent.setup();
    view();
    await user.type(await screen.findByLabelText("Job title contains"), "zzz");
    expect(await screen.findByText(/Database unavailable \(HTTP 503\)/)).toBeInTheDocument();
    expect(fn.mock.calls.some(([u]) => String(u).includes("job_title=zzz"))).toBe(true);
  });

  it("score range, eligibility and experience filters are sent to the API; the range cannot invert; reset restores the defaults", async () => {
    const fn = mockFetch(() => undefined, base());
    const user = userEvent.setup();
    view();
    await screen.findByText("G. Match results");
    const urls = () => fn.mock.calls.map(([u]) => String(u)).filter((u) => u.startsWith("/api/matching/results?"));
    expect(urls().at(-1)).toContain("latest_only=true");
    expect(urls().at(-1)).toContain("sort=score");
    await user.clear(screen.getByLabelText("Min score")); await user.type(screen.getByLabelText("Min score"), "60");
    await user.clear(screen.getByLabelText("Max score")); await user.type(screen.getByLabelText("Max score"), "85");
    await user.selectOptions(screen.getByLabelText("Eligibility"), "PASS");
    await user.type(screen.getByLabelText(/Max years of experience required/), "5");
    await waitFor(() => {
      const last = urls().at(-1)!;
      expect(last).toContain("min_score=60"); expect(last).toContain("max_score=85"); expect(last).toContain("eligibility=PASS"); expect(last).toContain("max_experience_required=5");
    });
    await user.clear(screen.getByLabelText("Max score")); await user.type(screen.getByLabelText("Max score"), "40");
    expect(screen.getByLabelText("Min score")).toHaveValue(60);                                    // never rewritten behind the user's back ...
    expect(screen.getByText(/Min score \(60\) is higher than Max score \(40\)/)).toBeInTheDocument();   // ... but the problem is explained
    await user.click(screen.getByRole("button", { name: "Reset filters" }));
    expect(screen.getByLabelText("Min score")).toHaveValue(0);
    expect(screen.getByLabelText("Max score")).toHaveValue(100);
    expect(screen.getByLabelText("Eligibility")).toHaveValue("Any");
  });

  it("the Applied switch records that YOU applied (after a confirmation), then shows Applied and cannot be turned off; the filter is sent", async () => {
    let applied = false;
    const row = { match_id: "m1", job_id: "a".repeat(32), created_at: "2026-11-05T08:00:00", job_title: "ML Engineer", company_name: "Acme", match_score: 74, classification: "Good Match",
      eligibility_status: "PASS", recommendation_code: "BELOW_THRESHOLD", data_coverage: 90, profile_version: 3, scoring_version: "1.1", min_experience_required: 3, role_fit: "same" };
    const fn = mockFetch((url, init) => {
      if (url.startsWith("/api/matching/results?") && isGet(init)) return json([{ ...row, application_state: applied ? "SUBMITTED" : null }]);
      if (url === "/api/applications/record-applied" && init?.method === "POST") { applied = true; return json({ state: "SUBMITTED" }); }
      return undefined;
    }, base());
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const user = userEvent.setup();
    view();
    const sw = await screen.findByRole("switch", { name: "Applied: ML Engineer at Acme" });
    expect(sw).not.toBeChecked();
    await user.click(sw);                                                                        // cancelled in the confirmation: nothing recorded
    expect(fn.mock.calls.some(([u]) => u === "/api/applications/record-applied")).toBe(false);
    await user.click(sw);
    await waitFor(() => expect(screen.getByRole("switch", { name: "Applied: ML Engineer at Acme" })).toBeChecked());
    expect(screen.getByRole("switch", { name: "Applied: ML Engineer at Acme" })).toBeDisabled();
    const call = fn.mock.calls.find(([u]) => u === "/api/applications/record-applied")!;
    expect(JSON.parse(String((call[1] as RequestInit).body))).toEqual({ job_id: "a".repeat(32) });
    expect(confirm.mock.calls[1][0]).toMatch(/does not apply for you/);
    await user.selectOptions(screen.getByLabelText("Application"), "Not applied yet");
    await waitFor(() => expect(fn.mock.calls.some(([u]) => String(u).startsWith("/api/matching/results?") && String(u).includes("applied=no"))).toBe(true));
    confirm.mockRestore();
  });
});
