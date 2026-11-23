import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";
import JobDiscovery from "./JobDiscovery";

type Handler = (url: URL, init?: RequestInit) => { status?: number; body: unknown } | Promise<{ status?: number; body: unknown }>;
function mockFetch(routes: Record<string, Handler>, calls: string[] = []) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push(`${init?.method ?? "GET"} ${url.pathname}${url.search}`);
    const h = routes[url.pathname];
    if (!h) return new Response(JSON.stringify({ detail: "not mocked" }), { status: 404, headers: { "content-type": "application/json" } });
    const r = await h(url, init);
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { "content-type": "application/json" } });
  }));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const SOURCES = [
  { source: "career_pages", status: "ENABLED", automated_discovery: true, manual_import: false, companies_configured: 0, jobs_stored: 0 },
  { source: "linkedin", status: "MANUAL_ONLY", automated_discovery: false, manual_import: true, reason: "No automated collection", basis: "Terms of service", jobs_stored: 1 },
];
const JOB = { job_id: "j1", job_title: "ML Engineer", company_name: "Acme", location: ["Pune"], work_mode: "remote", experience_min: 2, experience_max: 5, salary_min: null, salary_max: null, currency: null,
  posted_date: "2026-11-05T00:00:00", discovered_at: "2026-11-06T10:00:00", last_updated_at: "2026-11-06T10:00:00", sources: [{ source: "linkedin" }], source: "linkedin", status: "active",
  needs_review: false, dedup_status: "unique", dedup_reasons: [], mandatory_skills: ["Python"], preferred_skills: [], job_description: "Build models", warnings: [], job_url: "https://example.com/j1", employment_type: "full-time" };

const base = (over: Record<string, Handler> = {}): Record<string, Handler> => ({
  "/api/jobs/sources": () => ({ body: SOURCES }),
  "/api/jobs/discovery/status": () => ({ body: { status: "never_run" } }),
  "/api/jobs/discovery/runs": () => ({ body: [] }),
  "/api/jobs/page": (u) => ({ body: { total: 0, items: [], limit: Number(u.searchParams.get("limit")) } }),
  "/api/matching/results": () => ({ body: [] }),
  ...over,
});

describe("JobDiscovery page", () => {
  it("shows a loading state first", () => {
    mockFetch({ "/api/jobs/sources": () => new Promise(() => {}) as any });
    render(<JobDiscovery />);
    expect(screen.getByRole("heading", { name: /Job Discovery/ })).toBeInTheDocument();
    expect(screen.getAllByRole("progressbar").length).toBeGreaterThan(0);
  });

  it("shows sources, the empty job list and the empty history", async () => {
    mockFetch(base());
    render(<JobDiscovery />);
    expect((await screen.findAllByText("Company career pages")).length).toBeGreaterThan(0);
    expect(screen.getByText("✋ MANUAL_ONLY")).toBeInTheDocument();
    expect(screen.getByText(/no companies are configured/)).toBeInTheDocument();
    expect(await screen.findByText(/No jobs match/)).toBeInTheDocument();
    expect(await screen.findByText(/No discovery run yet/)).toBeInTheDocument();
    expect(screen.getByText("Terms of service", { exact: false })).toBeInTheDocument();
  });

  it("shows a clear error when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    render(<JobDiscovery />);
    expect((await screen.findAllByText(/Backend unreachable/)).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Traceback/)).not.toBeInTheDocument();
  });

  it("lists jobs with details, an https-only link and no refresh for manual jobs", async () => {
    const calls: string[] = [];
    mockFetch(base({
      "/api/jobs/page": (u) => ({ body: { total: 1, items: u.searchParams.get("limit") === "1" ? [] : [JOB] } }),
      "/api/jobs/j1": () => ({ body: JOB }),
      "/api/matching/results": () => ({ body: [{ job_id: "j1", match_score: 77 }] }),
    }), calls);
    render(<JobDiscovery />);
    expect(await screen.findByText(/1 of 1 job\(s\) shown/)).toBeInTheDocument();
    expect(await screen.findByText("77")).toBeInTheDocument();
    expect(await screen.findByText("Open original posting")).toHaveAttribute("href", "https://example.com/j1");
    expect(screen.getByText(/Refresh is only available for jobs from permitted automated sources/)).toBeInTheDocument();
    expect(screen.queryByText(/Refresh details from source/)).not.toBeInTheDocument();
  });

  it("does not render a link for a non-http job URL", async () => {
    mockFetch(base({
      "/api/jobs/page": (u) => ({ body: { total: 1, items: u.searchParams.get("limit") === "1" ? [] : [JOB] } }),
      "/api/jobs/j1": () => ({ body: { ...JOB, job_url: "javascript:alert(1)" } }),
    }));
    render(<JobDiscovery />);
    await screen.findByText(/Required skills:/);
    expect(screen.queryByText("Open original posting")).not.toBeInTheDocument();
  });

  it("refuses to start discovery when no source is automated, and imports manually with only filled fields", async () => {
    const calls: string[] = [];
    let importBody: any = null;
    mockFetch(base({
      "/api/jobs/sources": () => ({ body: [SOURCES[1]] }),
      "/api/jobs/import": (_u, init) => { importBody = JSON.parse(String(init?.body)); return { status: 200, body: { action: "insert" } }; },
    }), calls);
    render(<JobDiscovery />);
    const discover = await screen.findByRole("button", { name: "Discover jobs" });
    expect(discover).toBeDisabled();
    await userEvent.click(screen.getByText(/Import a job manually/));
    await userEvent.type(screen.getByLabelText("Job title"), "Data Scientist");
    await userEvent.click(screen.getByRole("button", { name: "Import job" }));
    await waitFor(() => expect(screen.getByText("Imported as a new job.")).toBeInTheDocument());
    expect(importBody).toEqual({ url: null, job: { job_title: "Data Scientist" } });
    expect(calls.some((c) => c.startsWith("POST /api/jobs/discover"))).toBe(false);
  });

  it("shows an Applied switch in front of each discovered job and records only after confirmation", async () => {
    let applied = false;
    const calls: string[] = [];
    mockFetch(base({
      "/api/jobs/page": (u) => ({ body: { total: 1, items: u.searchParams.get("limit") === "1" ? [] : [JOB] } }),
      "/api/jobs/j1": () => ({ body: JOB }),
      "/api/applications/states": () => ({ body: applied ? { j1: "SUBMITTED" } : {} }),
      "/api/applications/record-applied": () => { applied = true; return { body: { state: "SUBMITTED" } }; },
    }), calls);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<JobDiscovery />);
    const sw = await screen.findByRole("switch", { name: "Applied: ML Engineer at Acme" });
    expect(sw).not.toBeChecked();
    await userEvent.click(sw);
    await waitFor(() => expect(screen.getByRole("switch", { name: "Applied: ML Engineer at Acme" })).toBeChecked());
    expect(calls).toContain("POST /api/applications/record-applied");
    confirm.mockRestore();
  });
});
