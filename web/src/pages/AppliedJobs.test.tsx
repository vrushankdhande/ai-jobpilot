import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";
import AppliedJobs from "./AppliedJobs";

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

const SUMMARY = { applications: { submitted: 1, verified: 0, failed: 0, by_state: { VERIFICATION_PENDING: 1 } } };
const APP = (state: string) => ({ application_id: "a1", company_name: "Acme", job_title: "ML Engineer", state, mode: "automated", created_at: "2026-11-05T10:00:00", submitted_at: null, user_notes: "my note", provider: "local_test" });

function routes(state: string, extra: Record<string, Handler> = {}): Record<string, Handler> {
  return {
    "/api/dashboard/summary": () => ({ body: SUMMARY }),
    "/api/applications/page": (u) => ({ body: { total: 1, items: u.searchParams.get("limit") === "1" ? [] : [APP(state)] } }),
    "/api/applications/a1/status": () => ({ body: { state, evidence: [], reference: null, verified_by: null, detail: null, needs_manual_verification: state === "VERIFICATION_PENDING" } }),
    "/api/applications/a1": () => ({ body: { screenshots: [] } }),
    "/api/applications/a1/events": () => ({ body: [{ seq: 1, at: "2026-11-05T10:00:00", type: "created", from_state: null, to_state: "DISCOVERED", actor: "candidate" }] }),
    ...extra,
  };
}

describe("AppliedJobs page", () => {
  it("shows a loading state first", () => {
    mockFetch({ "/api/applications/page": () => new Promise(() => {}) as any, "/api/dashboard/summary": () => new Promise(() => {}) as any });
    render(<AppliedJobs />);
    expect(screen.getByRole("heading", { name: /Applied Jobs/ })).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("shows the empty state when nothing matches", async () => {
    mockFetch({ "/api/dashboard/summary": () => ({ body: SUMMARY }), "/api/applications/page": () => ({ body: { total: 0, items: [] } }) });
    render(<AppliedJobs />);
    expect(await screen.findByText(/No applications match\./)).toBeInTheDocument();
    expect(screen.getByText(/Prepare one on the Application Queue page\./)).toBeInTheDocument();
  });

  it("shows a clear error when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    render(<AppliedJobs />);
    expect(await screen.findByText(/Backend unreachable/)).toBeInTheDocument();
  });

  it("asks for the default 'applied' states and hides rejected applications", async () => {
    const calls: string[] = [];
    mockFetch(routes("VERIFIED"), calls);
    render(<AppliedJobs />);
    await screen.findByText("Details");
    const q = calls.find((c) => c.startsWith("GET /api/applications/page"))!;
    expect(q).toContain("states=SUBMITTED");
    expect(q).toContain("states=VERIFIED");
    expect(q).toContain("dismissed=false");
  });

  it("offers the outcome form for an unknown outcome and posts exactly what was chosen", async () => {
    let body: any = null;
    mockFetch(routes("VERIFICATION_PENDING", { "/api/applications/a1/confirm": (_u, init) => { body = JSON.parse(String(init?.body)); return { body: { ok: true } }; } }));
    render(<AppliedJobs />);
    expect(await screen.findByText(/We cannot tell whether this went through/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("It was NOT submitted"));
    await userEvent.type(screen.getByLabelText("Confirmation / reference number (if any)"), "REF-9");
    await userEvent.click(screen.getByRole("button", { name: "Record outcome" }));
    await waitFor(() => expect(body).toEqual({ submitted: false, reference: "REF-9", notes: null }));
  });

  it("has no outcome form for an approved (not yet submitted) application and saves notes", async () => {
    let notes: any = null;
    mockFetch(routes("APPROVED", { "/api/applications/a1/notes": (_u, init) => { notes = JSON.parse(String(init?.body)); return { body: { ok: true } }; } }));
    render(<AppliedJobs />);
    await screen.findByText("Details");
    expect(screen.queryByRole("button", { name: "Record outcome" })).not.toBeInTheDocument();
    const box = await screen.findByLabelText(/Your notes/);
    await userEvent.clear(box);
    await userEvent.type(box, "called HR");
    await userEvent.click(screen.getByRole("button", { name: /Save notes/ }));
    await waitFor(() => expect(notes).toEqual({ notes: "called HR" }));
  });

  it("shows the server's message when a note cannot be saved", async () => {
    mockFetch(routes("APPROVED", { "/api/applications/a1/notes": () => ({ status: 422, body: { detail: "notes too long" } }) }));
    render(<AppliedJobs />);
    await userEvent.click(await screen.findByRole("button", { name: /Save notes/ }));
    expect(await screen.findByText(/Not saved: notes too long \(HTTP 422\)/)).toBeInTheDocument();
  });
});
