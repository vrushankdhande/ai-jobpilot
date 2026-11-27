import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FlashProvider } from "../components/ui";
import Dashboard from "./Dashboard";

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
function mockApi(summary: any, counts: any) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
    const path = new URL(String(input), "http://localhost").pathname;
    if (path === "/api/dashboard/summary") return json(summary);
    if (path === "/api/matching/counts") return json(counts);
    if (path === "/api/scheduler/status") return json({ enabled: true, paused: false, automation_target: 10, jobs: [{ name: "daily_discovery", next_run_local: "2026-11-25T07:00:00+05:30" }],
      limits: { effective: { max_jobs_discovered_per_day: 500, max_jobs_processed_per_day: 30 }, caps: { max_jobs_discovered_per_day: 500, max_jobs_processed_per_day: 30 }, usage: { jobs_discovered: 17 } } });
    return new Response("{}", { status: 404 });
  }));
}
const SUMMARY = (apps: any = {}) => ({
  generated_at: "2026-11-24T15:00:00Z", timezone: "Asia/Kolkata", min_match_score: 70,
  jobs: { total: 422, discovered_today: 17, scored: 422, matching_profile: 5, average_match_score: 38.6 },
  applications: { awaiting_approval: 0, submitted: 22, verified: 0, failed: 0, pending_actions: { needs_your_input: 0, ready_to_approve: 0, approved_not_submitted: 0, verify_outcome: 0, manual_handoff: 0 }, pending_actions_total: 0, ...apps },
  queue: { completed: 4 }, interviews: { upcoming_7_days: 0, next: [] },
  activity: [{ date: "2026-11-17", jobs_discovered: 50 }, { date: "2026-11-23", jobs_discovered: 8 }, { date: "2026-11-24", jobs_discovered: 17, applications_submitted: 19 }],
  recent: [], system: { database: "connected", scheduler_enabled: true, scheduler_paused: false, last_discovery: null, notifications_enabled: false, ai_key_configured: false, automation_enabled: false },
});
const metric = (label: string) => screen.getAllByTestId("metric").find((m) => within(m).queryByText(label, { exact: true }));

beforeEach(() => { vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Dashboard", () => {
  it("opens with today's summary and lists zero boxes on one line instead of as cards", async () => {
    mockApi(SUMMARY(), { to_apply: 0, eligible_not_applied: 1, good_matches: 5, applied: 21 });
    render(<MemoryRouter><FlashProvider><Dashboard /></FlashProvider></MemoryRouter>);
    expect(await screen.findByText(/Nothing is waiting for you right now\. Next job search:/)).toBeInTheDocument();
    expect(within(metric("New jobs today")!).getByText("17")).toBeInTheDocument();
    expect(within(metric("New jobs this week")!).getByText("75")).toBeInTheDocument();    // 50 + 8 + 17 (series shorter than 7 days)
    expect(within(metric("Applied this week")!).getByText("19")).toBeInTheDocument();
    expect(within(metric("Submitted")!).getByText("22")).toBeInTheDocument();
    expect(metric("Verified")).toBeUndefined();                                            // a 0 is not a card...
    const zeros = screen.getByTestId("zero-status");
    for (const l of ["Awaiting approval", "Verified", "Failed", "Upcoming interviews (7 days)"]) expect(zeros.textContent).toContain(l);   // ...it is listed here
    expect(zeros.textContent).not.toContain("Submitted");
    // the activity charts say in words what they show, and the same numbers are available as a table
    expect(screen.getByTestId("activity-summary")).toHaveTextContent("Last 3 days: 75 jobs found, 0 applications prepared, 19 applied. Busiest day: 17 Nov (50 jobs found).");
    expect(screen.getByRole("figure", { name: "Jobs found per day" })).toBeInTheDocument();
    expect(screen.getByRole("figure", { name: "Applications per day" })).toBeInTheDocument();
    const table = screen.getByRole("table");
    const newest = within(within(table).getAllByRole("row")[1]).getAllByRole("cell").map((c) => c.textContent);
    expect(newest).toEqual(["24 Nov", "17", "0", "19"]);                                    // newest day first
  });

  it("puts work that needs the candidate at the top and shows those boxes as cards", async () => {
    mockApi(SUMMARY({ awaiting_approval: 2, pending_actions_total: 2, pending_actions: { needs_your_input: 0, ready_to_approve: 2, approved_not_submitted: 0, verify_outcome: 0, manual_handoff: 0 } }), { to_apply: 3 });
    render(<MemoryRouter><FlashProvider><Dashboard /></FlashProvider></MemoryRouter>);
    expect(await screen.findByText(/2 applications need you/)).toHaveTextContent("2 applications need you (input, approval or a manual step).");
    expect(screen.getByRole("link", { name: /Open Application Queue/ })).toHaveAttribute("href", "/application-queue");
    expect(within(metric("Awaiting approval")!).getByText("2")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "3" })).toHaveAttribute("href", "/job-matching");
    expect(metric("Good jobs to apply")).toContainElement(screen.getByRole("link", { name: "3" }));
  });
});
