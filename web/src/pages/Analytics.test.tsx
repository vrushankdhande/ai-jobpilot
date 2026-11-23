import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, vi } from "vitest";
import Analytics from "./Analytics";

type Handler = (url: URL) => any;
function mockFetch(routes: Record<string, Handler>, calls: string[] = []) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push(`${init?.method ?? "GET"} ${url.pathname}${url.search}`);
    const h = routes[url.pathname];
    if (!h) return new Response(JSON.stringify({ detail: "not mocked" }), { status: 404, headers: { "content-type": "application/json" } });
    const r = await h(url);
    if (r.csv !== undefined) return new Response(r.csv, { status: 200, headers: { "content-type": "text/csv" } });
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { "content-type": "application/json" } });
  }));
}
beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });      // recharts needs it; jsdom has none
});
afterEach(() => { cleanup(); });

const OVERVIEW = (over: any = {}) => ({
  from: "2026-10-15", to: "2026-11-13", days: 30, timezone: "Asia/Kolkata",
  totals: { jobs_discovered: 4, jobs_matched: 3, applications_prepared: 2, applications_submitted: 1 }, scored_jobs: 3, score_distribution: [{ range: "0-20", jobs: 1 }, { range: "80-100", jobs: 2 }],
  applications_by_status: { VERIFIED: 1, APPROVED: 1 }, daily: [{ date: "2026-11-12", jobs_discovered: 4, jobs_matched: 3 }], weekly: [{ week: "2026-W46", jobs_discovered: 4 }],
  application_outcomes: { attempts: 2, submitted: 1, success_rate: 50, failure_rate: null, unknown_rate: 0, confirmed: 1, self_reported: 0, pending_verification: 0, failed_after_send: 0, note: "Prepared applications are not attempts." },
  interview_conversion: { interviews: 1, completed: 0, advanced: 0, offers: 0, interviews_per_submission: 1, offer_rate_of_completed: null },
  sources: [{ source: "linkedin", jobs: 4, scored: 3, avg_match_score: 71.5, applications: 2, submitted: 1 }], ...over,
});

describe("Analytics page", () => {
  it("shows a loading state first", () => {
    mockFetch({ "/api/analytics/overview": () => new Promise(() => {}) });
    render(<Analytics />);
    expect(screen.getByRole("heading", { name: /Analytics/ })).toBeInTheDocument();
    expect(screen.getByText("Calculating…")).toBeInTheDocument();
  });

  it("shows empty states when the range has no records, and dashes for missing rates", async () => {
    mockFetch({
      "/api/analytics/overview": () => ({ body: OVERVIEW({ totals: { a: 0, b: 0 }, scored_jobs: 0, score_distribution: [], applications_by_status: {}, sources: [], daily: [], weekly: [],
        application_outcomes: { ...OVERVIEW().application_outcomes, success_rate: null, failure_rate: null, unknown_rate: null, submitted: 0, attempts: 0 } }) }),
      "/api/analytics/export": () => ({ csv: "" }),
    });
    render(<Analytics />);
    expect(await screen.findByText(/No records in this date range\./)).toBeInTheDocument();
    expect(screen.getByText(/No matches in this range\./)).toBeInTheDocument();
    expect(screen.getByText(/No applications created in this range\./)).toBeInTheDocument();
    expect(screen.getByText(/No source data in this range\./)).toBeInTheDocument();
    const success = screen.getByText("Success rate").closest("[data-testid=metric]")!;
    expect(success).toHaveTextContent("—");
  });

  it("shows a clear error when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    render(<Analytics />);
    expect(await screen.findByText(/Backend unreachable/)).toBeInTheDocument();
  });

  it("shows real figures, source performance, rates and the weekly tab", async () => {
    mockFetch({ "/api/analytics/overview": () => ({ body: OVERVIEW() }), "/api/analytics/export": () => ({ csv: "a,b\n1,2\n" }) });
    render(<Analytics />);
    expect(await screen.findByText(/30 days, Asia\/Kolkata/)).toBeInTheDocument();
    expect(screen.getByText("Success rate").closest("[data-testid=metric]")).toHaveTextContent("50%");
    expect(screen.getByText("Failure rate").closest("[data-testid=metric]")).toHaveTextContent("—");
    expect(screen.getByText("71.5")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Weekly" }));
    expect(screen.getByText("2026-W46")).toBeInTheDocument();
    expect(screen.getByText("Week")).toBeInTheDocument();
  });

  it("asks for each CSV export for the chosen range and enables only non-empty ones", async () => {
    const calls: string[] = [];
    mockFetch({ "/api/analytics/overview": () => ({ body: OVERVIEW() }), "/api/analytics/export": (u) => ({ csv: u.searchParams.get("kind") === "scores" ? "" : "a,b\n1,2\n" }) }, calls);
    render(<Analytics />);
    const daily = await screen.findByRole("button", { name: /Daily CSV/ });
    await waitFor(() => expect(daily).toBeEnabled());
    expect(screen.getByRole("button", { name: /Scores CSV/ })).toBeDisabled();
    for (const kind of ["daily", "weekly", "status", "sources", "scores"]) expect(calls.some((c) => c.includes(`/api/analytics/export?kind=${kind}&`))).toBe(true);
  });

  it("recomputes the range from a quick preset", async () => {
    const calls: string[] = [];
    mockFetch({ "/api/analytics/overview": () => ({ body: OVERVIEW() }), "/api/analytics/export": () => ({ csv: "x" }) }, calls);
    render(<Analytics />);
    await screen.findByText(/days are local days/);
    await userEvent.selectOptions(screen.getByLabelText("Quick range"), "Last 7 days");
    await waitFor(() => expect(calls.filter((c) => c.startsWith("GET /api/analytics/overview")).length).toBeGreaterThan(1));
    const last = calls.filter((c) => c.startsWith("GET /api/analytics/overview")).at(-1)!;
    const p = new URL("http://x" + last.replace("GET ", "")).searchParams;
    const days = (new Date(p.get("date_to")! + "T00:00:00Z").getTime() - new Date(p.get("date_from")! + "T00:00:00Z").getTime()) / 86400000;
    expect(days).toBe(6);
  });
});
