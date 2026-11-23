import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ApplicationQueue from "./ApplicationQueue";

interface Call { url: string; method: string; headers: Record<string, string>; body?: string }
let calls: Call[] = [];
let routes: Record<string, (c: Call) => unknown> = {};

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function installFetch(overrides: Record<string, (c: Call) => unknown> = {}) {
  routes = { ...overrides };
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init: RequestInit = {}) => {
    const c: Call = { url: String(input), method: init.method ?? "GET", headers: (init.headers as Record<string, string>) ?? {}, body: init.body as string | undefined };
    calls.push(c);
    const path = c.url.split("?")[0];
    const exact = `${c.method} ${path}`;
    const key = exact in routes ? exact : Object.keys(routes).find((k) => k.endsWith("*") && k.startsWith(`${c.method} `) && path.startsWith(k.slice(c.method.length + 1, -1)));
    if (key) { const out = routes[key](c); return out instanceof Response ? out : json(out); }
    return json({ detail: `unrouted ${c.method} ${path}` }, 404);
  }));
}

const JOBS = [{ job_id: "j1", job_title: "Local Form Engineer", company_name: "Local Test Co" }, { job_id: "j2", job_title: "LinkedIn Role", company_name: "Big Co" }];
const MATCHES = [{ job_id: "j1", match_score: 77.5, classification: "Good Match", eligibility: "PASS" }, { job_id: "j2", match_score: 62.7, classification: "Moderate Match", eligibility: "FAIL" }];
const MAPPING_BASE = [{ field_id: "full_name", label: "Full name", required: true, status: "filled", value: "Jane Sample", source: "contact.full_name", confidence: "high", kind: "text" }];

function appDetail(over: Record<string, unknown> = {}) {
  return {
    application_id: "a1", job_id: "j1", state: "READY_FOR_REVIEW", mode: "automated", notes: [], mapping: MAPPING_BASE, uploads: [], screenshots: [], form_summary: { fields: 1, filled: 1, needs_input: 0 },
    validation: { checks: [], missing_fields: [] }, ...over,
  };
}

/** The standard happy-path backend; each test overrides what it needs. */
function backend(app: Record<string, unknown> | null, extra: Record<string, (c: Call) => unknown> = {}, appsList?: () => unknown[]) {
  installFetch({
    "GET /web/config": () => ({ adminEnabled: true }),
    "GET /api/jobs": () => JOBS,
    "GET /api/matching/results": () => MATCHES,
    "GET /api/applications": () => (appsList ? appsList() : app ? [{ application_id: "a1", job_id: "j1", state: app.state, mode: app.mode }] : []),
    "GET /api/applications/page": () => ({ total: 0, items: [] }),
    "GET /api/queue": () => ({ counts: {}, tasks: [] }),
    "GET /api/applications/policy/*": (c) => ({ automation_allowed: c.url.includes("/j1"), provider: "local_test", reason: "manual: not permitted", apply_url: "https://example.com/apply" }),
    "GET /api/documents": () => [{ version: 1, review_status: "approved" }],
    "GET /api/applications/a1": () => app,
    "GET /api/applications/a1/status": () => ({ outcome: null }),
    "GET /api/applications/a1/events": () => [],
    ...extra,
  });
}

const renderPage = (path = "/application-queue") => render(<MemoryRouter initialEntries={[path]}><ApplicationQueue /></MemoryRouter>);
const waitReview = () => screen.findByRole("heading", { name: "4. Review" });
const btn = (re: RegExp) => screen.queryByRole("button", { name: re });

beforeEach(() => { vi.useRealTimers(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("(a) manual handoff / restricted applications", () => {
  it("shows no Approve and no Submit for a manual-handoff application", async () => {
    backend(appDetail({ state: "MANUAL_HANDOFF_REQUIRED", mode: "manual_handoff", handoff: { apply_url: "https://example.com/apply", documents: {}, prepared_answers: [] } }));
    renderPage();
    await waitReview();
    expect(await screen.findByText(/You apply \(manual handoff\)/)).toBeInTheDocument();
    expect(btn(/Approve exactly/)).toBeNull();
    expect(btn(/Submit application/)).toBeNull();
    expect(screen.getByRole("heading", { name: "I applied myself" })).toBeInTheDocument();
  });

  it("shows no Approve/Submit even if a manual-mode application sits in READY_FOR_REVIEW or APPROVED", async () => {
    for (const state of ["READY_FOR_REVIEW", "APPROVED"]) {
      backend(appDetail({ state, mode: "manual_handoff" }));
      const { unmount } = renderPage();
      await waitReview();
      expect(btn(/Approve exactly/)).toBeNull();
      expect(btn(/Submit application/)).toBeNull();
      unmount();
    }
  });

  it("never offers Submit before approval (READY_FOR_REVIEW, automated)", async () => {
    backend(appDetail({ state: "READY_FOR_REVIEW" }));
    renderPage();
    await waitReview();
    expect(await screen.findByRole("button", { name: /Approve exactly/ })).toBeEnabled();
    expect(btn(/Submit application/)).toBeNull();
  });
});

describe("(b) submit needs the explicit tick", () => {
  it("is disabled until the 'I understand' box is ticked, then POSTs once with the admin marker", async () => {
    backend(appDetail({ state: "APPROVED" }), { "POST /api/applications/a1/submit": () => ({ state: "SUBMITTED" }) });
    renderPage();
    const submit = await screen.findByRole("button", { name: /Submit application/ });
    expect(submit).toBeDisabled();
    await userEvent.click(screen.getByLabelText("I understand and want to submit this application now"));
    expect(submit).toBeEnabled();
    expect(calls.filter((c) => c.method === "POST" && c.url.endsWith("/submit"))).toHaveLength(0);              // nothing sent yet
    await userEvent.click(submit);
    await waitFor(() => expect(calls.filter((c) => c.method === "POST" && c.url.endsWith("/submit"))).toHaveLength(1));
    expect(btn(/Approve exactly/)).toBeNull();
  });

  it("is not shown at all when the application is not APPROVED (e.g. already SUBMITTED)", async () => {
    backend(appDetail({ state: "SUBMITTED" }));
    renderPage();
    await waitReview();
    expect(btn(/Submit application/)).toBeNull();
    expect(btn(/Approve exactly/)).toBeNull();
  });
});

describe("(c) approve needs the warnings acknowledged", () => {
  it("stays disabled until the acknowledgement is ticked", async () => {
    backend(appDetail({ validation: { checks: [{ name: "review_suggested", status: "warn", critical: false, message: "double-check" }], missing_fields: [] } }),
      { "POST /api/applications/a1/approve": () => ({ state: "APPROVED" }) });
    renderPage();
    const approve = await screen.findByRole("button", { name: /Approve exactly this application/ });
    expect(approve).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/I have reviewed the values above and the warnings/));
    expect(approve).toBeEnabled();
    await userEvent.click(approve);
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/approve"))).toBe(true));
    const post = calls.find((c) => c.url.endsWith("/approve"))!;
    expect(JSON.parse(post.body!)).toEqual({ acknowledge_warnings: true });
  });

  it("is enabled straight away when there are no warnings, and sends acknowledge_warnings=false", async () => {
    backend(appDetail(), { "POST /api/applications/a1/approve": () => ({ state: "APPROVED" }) });
    renderPage();
    const approve = await screen.findByRole("button", { name: /Approve exactly this application/ });
    expect(approve).toBeEnabled();
    expect(screen.queryByLabelText(/I have reviewed the values above and the warnings/)).toBeNull();
    await userEvent.click(approve);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/approve"))).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url.endsWith("/approve"))!.body!)).toEqual({ acknowledge_warnings: false });
  });
});

describe("(d) the selected job survives state changes", () => {
  it("keeps the chosen job and its label when the application state changes after an action", async () => {
    let state = "AWAITING_USER_INPUT";
    const list = () => [{ application_id: "a2", job_id: "j2", state, mode: "automated" }];
    backend(null, {
      "GET /api/applications/a2": () => appDetail({ application_id: "a2", job_id: "j2", state }),
      "GET /api/applications/a2/status": () => ({ outcome: null }),
      "GET /api/applications/a2/events": () => [],
      "POST /api/applications/a2/validate": () => { state = "READY_FOR_REVIEW"; return {}; },
    }, list);
    renderPage();
    const select = (await screen.findByLabelText("Select a job")) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(2));
    await userEvent.selectOptions(select, "j2");
    expect(select.value).toBe("j2");
    expect(await screen.findByText(/Needs your input/, { selector: "h3" })).toBeInTheDocument();
    // labels carry only title - company [score], never the state
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(["Local Form Engineer - Local Test Co  [77.5]", "LinkedIn Role - Big Co  [62.7]"]);
    await userEvent.click(await screen.findByRole("button", { name: /Re-check the form now/ }));
    expect(await screen.findByText(/Ready for your review/, { selector: "h3" })).toBeInTheDocument();             // state changed ...
    expect((screen.getByLabelText("Select a job") as HTMLSelectElement).value).toBe("j2");                         // ... and the selection did not move
    expect(Array.from((screen.getByLabelText("Select a job") as HTMLSelectElement).options).map((o) => o.textContent)).toEqual(["Local Form Engineer - Local Test Co  [77.5]", "LinkedIn Role - Big Co  [62.7]"]);
  });

  it("opens the job given in the URL (?job=)", async () => {
    backend(null);
    renderPage("/application-queue?job=j2");
    const select = (await screen.findByLabelText("Select a job")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("j2"));
  });
});

describe("(e) consent and legal questions are never pre-answered", () => {
  it("renders checkbox / select / text answers all empty and sends only what the candidate fills in", async () => {
    const mapping = [
      ...MAPPING_BASE,
      { field_id: "privacy", label: "I agree to the privacy policy", required: true, status: "manual_required", kind: "checkbox" },
      { field_id: "relocate", label: "Are you willing to relocate?", required: true, status: "needs_input", kind: "select", options: ["Yes", "No"] },
      { field_id: "about", label: "Tell us about yourself", required: false, status: "needs_input", kind: "text" },
    ];
    backend(appDetail({ state: "AWAITING_USER_INPUT", mapping }), { "PUT /api/applications/a1/answers": () => ({}) });
    renderPage();
    const form = await screen.findByRole("form", { name: "Your answers" });
    const consent = within(form).getByLabelText("I agree to the privacy policy *") as HTMLInputElement;
    expect(consent.checked).toBe(false);
    expect((within(form).getByLabelText("Are you willing to relocate? *") as HTMLSelectElement).value).toBe("");
    expect((within(form).getByLabelText("Tell us about yourself") as HTMLInputElement).value).toBe("");
    // saving untouched sends NOTHING
    await userEvent.click(within(form).getByRole("button", { name: /Save answers and re-check/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.method === "PUT")!.body!)).toEqual({ answers: {} });
    // the candidate answers one thing
    await userEvent.click(within(form).getByLabelText("I agree to the privacy policy *"));
    await userEvent.click(within(form).getByRole("button", { name: /Save answers and re-check/ }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(2));
    expect(JSON.parse(calls.filter((c) => c.method === "PUT")[1].body!)).toEqual({ answers: { privacy: true } });
  });

  it("no checkbox on the page is pre-ticked on first load", async () => {
    backend(appDetail({ state: "AWAITING_USER_INPUT", mapping: [...MAPPING_BASE, { field_id: "privacy", label: "I agree", required: true, status: "manual_required", kind: "checkbox" }] }));
    renderPage();
    await screen.findByRole("form", { name: "Your answers" });
    for (const box of screen.getAllByRole("checkbox")) expect((box as HTMLInputElement).checked).toBe(false);
  });
});

describe("(f) the browser never holds the admin token", () => {
  it("marks approve and submit with X-Admin-Action and never sends X-Admin-Token on any call", async () => {
    backend(appDetail({ state: "READY_FOR_REVIEW" }), {
      "POST /api/applications/a1/approve": () => { return {}; },
      "POST /api/applications/a1/submit": () => ({}),
    });
    const { unmount } = renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Approve exactly this application/ }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/approve"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/approve"))!.headers["X-Admin-Action"]).toBe("1");
    unmount();

    backend(appDetail({ state: "APPROVED" }), { "POST /api/applications/a1/submit": () => ({}) });
    renderPage();
    await userEvent.click(await screen.findByLabelText("I understand and want to submit this application now"));
    await userEvent.click(screen.getByRole("button", { name: /Submit application/ }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/submit"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/submit"))!.headers["X-Admin-Action"]).toBe("1");

    for (const c of calls) {
      const names = Object.keys(c.headers).map((h) => h.toLowerCase());
      expect(names).not.toContain("x-admin-token");
    }
  });

  it("read-only calls and the prepare / reject calls carry no admin marker", async () => {
    backend(appDetail({ state: "READY_FOR_REVIEW" }), { "POST /api/applications/a1/reject": () => ({}), "POST /api/applications/prepare": () => ({}) });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Prepare again/ }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/prepare"))).toBe(true));
    for (const c of calls.filter((c) => c.method === "GET" || c.url.endsWith("/prepare"))) expect(c.headers["X-Admin-Action"]).toBeUndefined();
  });
});

describe("other rules", () => {
  it("reject needs a second, explicit confirmation", async () => {
    backend(appDetail(), { "POST /api/applications/a1/reject": () => ({}) });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Reject this application/ }));
    expect(calls.some((c) => c.url.endsWith("/reject"))).toBe(false);                      // the first click only asks
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Yes, confirm" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/reject"))).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url.endsWith("/reject"))!.body!)).toEqual({ reason: null });
  });

  it("shows the preparing message while preparing and prepare sends the acknowledgements as chosen", async () => {
    let release: (v: unknown) => void = () => {};
    backend(null, { "POST /api/applications/prepare": () => new Promise((r) => { release = r; }) as any });
    // a route returning a promise: wrap manually
    const original = (globalThis.fetch as any);
    vi.stubGlobal("fetch", vi.fn(async (input: string, init: RequestInit = {}) => {
      if (String(input).endsWith("/api/applications/prepare")) {
        calls.push({ url: String(input), method: "POST", headers: (init.headers as any) ?? {}, body: init.body as string });
        await new Promise((r) => { release = r as any; });
        return json({ application_id: "a1" });
      }
      return original(input, init);
    }));
    renderPage();
    await userEvent.click(await screen.findByLabelText("This may duplicate another application - continue"));
    await userEvent.click(await screen.findByRole("button", { name: /Prepare application/ }));
    expect(await screen.findByText(/Preparing: checking permissions, detecting the form and filling it from your verified data…/)).toBeInTheDocument();
    expect(JSON.parse(calls.find((c) => c.url.endsWith("/prepare"))!.body!)).toMatchObject({ job_id: "j1", allow_unreviewed: false, acknowledge_ineligible: false, acknowledge_possible_duplicate: true });
    release(null);
    await waitFor(() => expect(screen.queryByText(/Preparing: checking permissions/)).toBeNull());
  });

  it("the eligibility acknowledgement is only available when the job FAILED the check", async () => {
    backend(null);
    renderPage();
    const box = (await screen.findByLabelText("I know this job failed the eligibility check - prepare anyway")) as HTMLInputElement;
    await waitFor(() => expect(box).toBeDisabled());                     // j1 is PASS
    await userEvent.selectOptions(screen.getByLabelText("Select a job"), "j2");
    await waitFor(() => expect((screen.getByLabelText("I know this job failed the eligibility check - prepare anyway") as HTMLInputElement).disabled).toBe(false));
    expect((screen.getByLabelText("I know this job failed the eligibility check - prepare anyway") as HTMLInputElement).checked).toBe(false);
  });

  it("a backend error from prepare is shown, not hidden", async () => {
    backend(null, { "POST /api/applications/prepare": () => json({ detail: { message: "Eligibility check failed" } }, 409) });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Prepare application/ }));
    expect(await screen.findByText(/Eligibility check failed \(HTTP 409\)/)).toBeInTheDocument();
  });

  it("an unreachable backend gives a clear message", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    renderPage();
    expect((await screen.findAllByText(/Backend unreachable/)).length).toBeGreaterThan(0);
  });

  it("states tab shows an empty state and queue controls are disabled without ADMIN_TOKEN", async () => {
    backend(null, { "GET /web/config": () => ({ adminEnabled: false }), "GET /api/queue": () => ({ counts: {}, tasks: [{ task_id: "t1", status: "queued", job_title: "X", attempts: 0, max_attempts: 2 }] }) });
    renderPage();
    expect(await screen.findByText(/Nothing here\./)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Queued & processing" }));
    expect(await screen.findByRole("button", { name: /Pause/ })).toBeDisabled();
    expect(screen.getByText(/Queue controls need ADMIN_TOKEN/)).toBeInTheDocument();
  });

  it("lands on 'Needs your action', which shows a manual-handoff application, and Open selects that job", async () => {
    backend(null, { "GET /api/applications/page": (c) => (c.url.includes("MANUAL_HANDOFF_REQUIRED")
      ? { total: 1, items: [{ application_id: "a2", job_id: "j2", job_title: "LinkedIn Role", company_name: "Big Co", state: "MANUAL_HANDOFF_REQUIRED", mode: "manual_handoff", match_score: 62.7, eligibility: "PASS" }] }
      : { total: 0, items: [] }) });
    renderPage();
    expect(await screen.findByRole("tab", { name: "Needs your action", selected: true })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Open LinkedIn Role" }));
    await waitFor(() => expect((screen.getByLabelText("Select a job") as HTMLSelectElement).value).toBe("j2"));
  });
});
