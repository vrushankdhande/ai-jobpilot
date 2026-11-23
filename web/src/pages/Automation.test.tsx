import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FlashProvider } from "../components/ui";
import Automation from "./Automation";

type Call = { method: string; url: string; headers: Headers; body: any };
function mockFetch(routes: Record<string, unknown>, calls: Call[] = []) {
  vi.stubGlobal("fetch", vi.fn(async (input: any, init?: RequestInit) => {
    const url = String(input), path = url.split("?")[0];
    calls.push({ method: init?.method ?? "GET", url, headers: new Headers(init?.headers as any), body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const hit = routes[`${init?.method ?? "GET"} ${path}`] ?? routes[path];
    if (hit === undefined) return new Response(JSON.stringify({ detail: `no route ${path}` }), { status: 404, headers: { "content-type": "application/json" } });
    if (hit instanceof Response) return hit.clone();
    return new Response(JSON.stringify(hit), { status: 200, headers: { "content-type": "application/json" } });
  }));
  return calls;
}
const fail = (status: number, detail: unknown) => new Response(JSON.stringify({ detail }), { status, headers: { "content-type": "application/json" } });
const renderPage = () => render(<MemoryRouter><FlashProvider><Automation /></FlashProvider></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());

const STATUS = {
  enabled: true, paused: false, timezone: "Asia/Kolkata", now_local: "2026-11-13T10:00:00", automation_target: 3,
  jobs: [{ name: "daily_discovery", schedule: "07:00", last_run_at: null, next_run_at: "2026-11-14T01:30:00Z", next_run_local: "07:00", running: false, last_status: null, last_error: null }],
  limits: { effective: { max_jobs_discovered_per_day: 50, max_jobs_processed_per_day: 20, max_applications_per_day: 3, max_retries_per_application: 2, min_match_score: 70 },
    caps: { max_jobs_discovered_per_day: 100, max_jobs_processed_per_day: 30, max_applications_per_day: 5, max_retries_per_application: 3, min_match_score: 60 }, usage: { jobs_discovered: 4, jobs_processed: 1, applications_submitted: 0 } },
};
const TASK = { task_id: "t1", job_title: "ML Engineer", company_name: "Acme", match_score: 82, eligibility: "PASS", status: "awaiting_approval", attempts: 1, max_attempts: 3, last_error: null, updated_at: "2026-11-13T09:00:00Z" };
const FULL = {
  "/api/scheduler/status": STATUS, "/web/config": { adminEnabled: true },
  "/api/queue": { counts: { awaiting_approval: 1 }, tasks: [TASK] },
  "/api/notifications": { config: { configured: false, recipient: "" }, notifications: [] },
  "/api/reports/daily": [],
};

describe("Automation page", () => {
  it("shows a loading indicator first", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    renderPage();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("shows the error and stops when the scheduler status cannot be loaded", async () => {
    mockFetch({ ...FULL, "/api/scheduler/status": fail(503, "Database unavailable") });
    renderPage();
    expect(await screen.findByText(/Database unavailable \(HTTP 503\)/)).toBeInTheDocument();
    expect(screen.queryByText("1. Scheduler")).toBeNull();
  });

  it("renders every section with data and uses the empty states for the rest", async () => {
    mockFetch(FULL);
    renderPage();
    expect(await screen.findByText("1. Scheduler")).toBeInTheDocument();
    expect(screen.getByText(/🟢 Running/)).toBeInTheDocument();
    expect(screen.getByText("Daily Discovery")).toBeInTheDocument();
    expect(screen.getByText("Jobs discovered today")).toBeInTheDocument();
    expect(await screen.findByText(/Awaiting your approval: 1/)).toBeInTheDocument();
    expect(screen.getByText(/No failed or unverified tasks./)).toBeInTheDocument();
    expect(screen.getByText(/Not configured - the system works without e-mail/)).toBeInTheDocument();
    expect(await screen.findByText(/No reports yet./)).toBeInTheDocument();
  });

  it("has NO approve or submit control anywhere: the worker only prepares, and an awaiting-approval task only links to the Application Queue", async () => {
    mockFetch(FULL);
    renderPage();
    await screen.findByText(/Awaiting your approval: 1/);
    for (const b of screen.getAllByRole("button")) expect(b.textContent ?? "").not.toMatch(/approve|submit/i);
    expect(screen.queryByRole("button", { name: /approve|submit/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /approve|submit/i })).toBeNull();
    expect(screen.getByRole("link", { name: "Application Queue" })).toHaveAttribute("href", "/application-queue");
    expect(screen.getByRole("button", { name: /Prepare queued applications now/ })).toBeInTheDocument();
  });

  it("read-only without an admin token: warning shown and every control disabled", async () => {
    mockFetch({ ...FULL, "/web/config": { adminEnabled: false } });
    renderPage();
    expect(await screen.findByText(/ADMIN_TOKEN is not set/)).toBeInTheDocument();
    await screen.findByText(/Awaiting your approval: 1/);
    for (const name of [/Start/, /Stop/, /Discover now/, /Prepare queued/, /Save limits/, /Pause$/, /Cancel/, /Generate today/]) {
      for (const b of screen.getAllByRole("button", { name })) expect(b).toBeDisabled();
    }
  });

  it("controls are admin calls (action marker, never a token) and a refused call shows the server's message", async () => {
    const calls = mockFetch({ ...FULL, "POST /api/scheduler/pause": fail(403, "Admin token required"), "POST /api/scheduler/discover": { ok: true } });
    renderPage();
    await screen.findByText("1. Scheduler");
    await waitFor(() => expect(screen.getByRole("button", { name: /Discover now/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /Pause automation/ }));
    expect(await screen.findByText(/Admin token required \(HTTP 403\)/)).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST" && c.url === "/api/scheduler/pause")!;
    expect(post.headers.get("X-Admin-Action")).toBe("1");
    expect(post.headers.get("X-Admin-Token")).toBeNull();
  });

  it("limits form sends only numbers and can only go up to the caps", async () => {
    const calls = mockFetch({ ...FULL, "PUT /api/scheduler/limits": { ok: true } });
    renderPage();
    const field = await screen.findByLabelText("Applications submitted / day");
    expect(field).toHaveAttribute("max", "5");
    await waitFor(() => expect(screen.getByRole("button", { name: "Save limits" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Save limits" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body.limits).toMatchObject({ max_applications_per_day: 3, min_match_score: 70 });
  });
});
