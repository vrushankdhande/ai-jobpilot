import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FlashProvider } from "../components/ui";
import Settings from "./Settings";

type Route = unknown | ((init?: RequestInit) => unknown);
function mockFetch(routes: Record<string, Route>, calls: Array<{ method: string; url: string; headers: Headers; body: any }> = []) {
  const fn = vi.fn(async (input: any, init?: RequestInit) => {
    const url = String(input);
    const path = url.split("?")[0];
    calls.push({ method: init?.method ?? "GET", url, headers: new Headers(init?.headers as any), body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const key = `${init?.method ?? "GET"} ${path}`;
    const hit = routes[key] ?? routes[path];
    if (hit === undefined) return new Response(JSON.stringify({ detail: `no route ${key}` }), { status: 404, headers: { "content-type": "application/json" } });
    const r: any = typeof hit === "function" ? (hit as any)(init) : hit;
    if (r instanceof Response) return r;
    return new Response(JSON.stringify(r), { status: 200, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}
const fail = (status: number, detail: unknown) => new Response(JSON.stringify({ detail }), { status, headers: { "content-type": "application/json" } });

const CONFIG = {
  preferences: { titles: ["ML Engineer"], locations: [], skills: [], companies: [], remote: null, hybrid: null, onsite: null, open_to_relocation: null, experience_years_min: null, experience_years_max: null,
    min_salary: null, salary_currency: null, notice_period: null, posted_within_days: null, employment_types: [], work_authorization: [], keywords_include: [], keywords_exclude: [] },
  scoring: { scoring_version: "v3", config_hash: "abc123", weights: { skills: 60, experience: 40 }, thresholds: { excellent: 85, strong: 75, good: 65, moderate: 50 }, recommendation_threshold: 70, min_data_coverage: 40, missing_data_policy: "normalize" },
  automation: { caps: { max_applications_per_day: 5, max_applications_per_platform_per_day: 3, daily_application_target: 3, max_jobs_discovered_per_day: 100, max_jobs_processed_per_day: 30, max_concurrent_workers: 1, min_match_score: 70,
    discovery_window: ["06:00", "22:00"], application_window: ["09:00", "21:00"] }, effective: { max_applications_per_day: 5, max_jobs_processed_per_day: 30, min_match_score: 70 } },
  scheduler: { enabled: false, timezone: "Asia/Kolkata", discovery_time: "07:00", report_time: "20:00", autostart: false },
  retry: { max_retries_per_application: 2, retry_backoff_seconds: 60, max_submit_retries: 1 },
  browser: { headless: true, channel: "", timeout_ms: 15000, slow_mo_ms: 0, persist_sessions: false },
  notifications: { enabled: false, configured: false, recipient: "", credentials_stored: false, setup_command: "uv run python scripts/gmail_setup.py" },
  ai: { provider: "anthropic", api_key_configured: false, model: "claude-sonnet-5-5", active: false, matching_enabled: false, matching_consent: false, extraction_enabled: false, documents_consent: false },
  sources: [], application_platforms: [], authorized_sites: [], companies: [],
  security: { admin_token_configured: true, secret_key_configured: true, master_automation_switch: false }, restart_note: "Restart the API after env changes.",
};

function renderPage() {
  return render(<MemoryRouter><FlashProvider><Settings /></FlashProvider></MemoryRouter>);
}
afterEach(() => vi.unstubAllGlobals());

describe("Settings page", () => {
  it("shows a loading indicator while the configuration loads", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    renderPage();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("shows the server's error message, not a stack trace, when the configuration cannot be loaded", async () => {
    mockFetch({ "/api/settings": fail(500, "Database unavailable"), "/api/resumes": [], "/web/config": { adminEnabled: true } });
    renderPage();
    expect(await screen.findByText(/Database unavailable \(HTTP 500\)/)).toBeInTheDocument();
    expect(screen.queryByText(/Traceback/)).toBeNull();
  });

  it("is read-only without an admin token: warning shown and every save button disabled", async () => {
    mockFetch({ "/api/settings": CONFIG, "/api/resumes": [], "/web/config": { adminEnabled: false } });
    renderPage();
    expect(await screen.findByText(/Read-only: set ADMIN_TOKEN/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save preferences/ })).toBeDisabled();
    expect(screen.getByText(/no active resume yet/)).toBeInTheDocument();
  });

  it("saves preferences as an admin call: the browser sends the action marker and NEVER an admin token", async () => {
    const calls = mockFetch({ "/api/settings": CONFIG, "/api/resumes": [{ resume_id: "r1", is_active: true, original_filename: "cv.pdf" }], "/web/config": { adminEnabled: true },
      "PUT /api/settings/preferences": { saved: true } });
    renderPage();
    const btn = await screen.findByRole("button", { name: /Save preferences/ });
    await waitFor(() => expect(btn).toBeEnabled());
    expect(screen.getByText("cv.pdf")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/Salary currency/), "inr");
    await userEvent.click(btn);
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url === "/api/settings/preferences")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.headers.get("X-Admin-Action")).toBe("1");
    expect(put.headers.get("X-Admin-Token")).toBeNull();
    expect(put.body.values).toMatchObject({ titles: ["ML Engineer"], salary_currency: "INR", remote: null });
  });

  it("shows the server's validation problems when a save is refused", async () => {
    mockFetch({ "/api/settings": CONFIG, "/api/resumes": [], "/web/config": { adminEnabled: true },
      "PUT /api/settings/preferences": fail(422, { message: "Invalid preferences", problems: ["min_salary needs a salary_currency"] }) });
    renderPage();
    const btn = await screen.findByRole("button", { name: /Save preferences/ });
    await waitFor(() => expect(btn).toBeEnabled());
    await userEvent.click(btn);
    expect(await screen.findByText(/Not saved: Invalid preferences/)).toBeInTheDocument();
    expect(screen.getByText("min_salary needs a salary_currency")).toBeInTheDocument();
  });

  it("matching: the weights total updates live and an invalid total disables saving", async () => {
    mockFetch({ "/api/settings": CONFIG, "/api/resumes": [], "/web/config": { adminEnabled: true } });
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Matching" }));
    expect(screen.getByText(/Weights total 100/)).toBeInTheDocument();
    const save = screen.getByRole("button", { name: /Save matching settings/ });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Skills"), { target: { value: "70" } });
    expect(screen.getByText(/Weights total 110/)).toBeInTheDocument();
    expect(save).toBeDisabled();
  });

  it("sources tab: platform permissions are read-only (no save control) and an empty company list shows an empty state", async () => {
    mockFetch({ "/api/settings": CONFIG, "/api/resumes": [], "/web/config": { adminEnabled: true } });
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Sources & platforms" }));
    expect(screen.getByText(/READ-ONLY here/)).toBeInTheDocument();
    expect(screen.getByText(/No companies configured\./)).toBeInTheDocument();
    expect(screen.getByText(/everything is manual handoff/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save/ })).toBeNull();
  });

  it("security and AI tabs show only whether secrets are configured, never a value or an input for one", async () => {
    mockFetch({ "/api/settings": CONFIG, "/api/resumes": [], "/web/config": { adminEnabled: true } });
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Security" }));
    expect(screen.getAllByText("configured").length).toBeGreaterThan(0);
    expect(screen.queryByLabelText(/token|secret|api key|password/i)).toBeNull();
    await userEvent.click(screen.getByRole("tab", { name: "AI" }));
    expect(screen.getByText("not configured")).toBeInTheDocument();
    expect(document.querySelector('input[type="password"]')).toBeNull();
  });
});
