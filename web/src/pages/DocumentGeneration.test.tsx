import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FlashProvider } from "../components/ui";
import DocumentGeneration from "./DocumentGeneration";

type Call = { method: string; url: string; headers: Headers; body: any };
function mockFetch(routes: Record<string, unknown>, calls: Call[] = []) {
  vi.stubGlobal("fetch", vi.fn(async (input: any, init?: RequestInit) => {
    const url = String(input), path = url.split("?")[0];
    calls.push({ method: init?.method ?? "GET", url, headers: new Headers(init?.headers as any), body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const hit = routes[`${init?.method ?? "GET"} ${path}`] ?? routes[path];
    if (hit === undefined) return new Response(JSON.stringify({ detail: `no route ${path}` }), { status: 404, headers: { "content-type": "application/json" } });
    if (typeof hit === "function") return new Response(JSON.stringify((hit as any)(url, init)), { status: 200, headers: { "content-type": "application/json" } });
    if (hit instanceof Response) return hit.clone();
    return new Response(JSON.stringify(hit), { status: 200, headers: { "content-type": "application/json" } });
  }));
  return calls;
}
const fail = (status: number, detail: unknown) => new Response(JSON.stringify({ detail }), { status, headers: { "content-type": "application/json" } });
const renderPage = () => render(<MemoryRouter><FlashProvider><DocumentGeneration /></FlashProvider></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());

const RESUME = { resume_id: "r1", is_active: true, original_filename: "jane_cv.pdf" };
const PROFILE = { review_status: "reviewed", profile_version: 3 };
const JOB = { job_id: "j1", job_title: "ML Engineer", company_name: "Acme" };
const LETTER = (validation: any, review = "draft") => ({
  document_id: "d1", version: 2, review_status: review, generator: "deterministic", ai_model: null, profile_stale: false, notes: ["Written from the fact pack"], exports: [],
  validation, content: { salutation: "Dear Hiring Manager,", paragraphs: [{ text: "I build models.", sources: ["p1"] }], signature: "Jane" },
});
const BASE = { "/api/resumes": [RESUME], "/api/resumes/r1/profile": PROFILE, "/api/documents/ai-status": { enabled: false, reason: "no API key" }, "/api/jobs": [JOB],
  "/api/jobs/j1": { ...JOB, mandatory_skills: ["Python"], preferred_skills: [], job_description: "Build things" } };

describe("Document Generation page", () => {
  it("shows a loading indicator first", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    renderPage();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("shows the server error when resumes cannot be loaded", async () => {
    mockFetch({ "/api/resumes": fail(503, "Database unavailable") });
    renderPage();
    expect(await screen.findByText(/Database unavailable \(HTTP 503\)/)).toBeInTheDocument();
  });

  it("empty: no active resume stops the page with a pointer to Resume Manager; no jobs shows the empty message", async () => {
    mockFetch({ "/api/resumes": [{ ...RESUME, is_active: false }] });
    const { unmount } = renderPage();
    expect(await screen.findByText(/No active resume/)).toBeInTheDocument();
    expect(screen.queryByText("2. Job vacancy")).toBeNull();
    unmount();
    mockFetch({ ...BASE, "/api/jobs": [] });
    renderPage();
    expect(await screen.findByText(/No jobs yet/)).toBeInTheDocument();
  });

  it("an unreviewed profile blocks generation until the user explicitly confirms", async () => {
    mockFetch({ ...BASE, "/api/resumes/r1/profile": { ...PROFILE, review_status: "needs_review" }, "/api/documents": [] });
    renderPage();
    expect(await screen.findByText(/not reviewed/)).toBeInTheDocument();
    const gen = await screen.findByRole("button", { name: /Generate resume/ });
    expect(gen).toBeDisabled();
    await userEvent.click(screen.getByLabelText("Use the unreviewed profile anyway"));
    expect(gen).toBeEnabled();
  });

  it("no document yet: shows the empty state and generates with the verified-profile request (candidate inputs only when typed)", async () => {
    const calls = mockFetch({ ...BASE, "/api/documents": [], "POST /api/documents/screening/generate": { document_id: "d9" } });
    renderPage();
    expect(await screen.findByText(/No resume for this job yet/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Screening answers" }));
    await userEvent.type(await screen.findByLabelText("Notice period"), "30 days");
    await userEvent.click(screen.getByRole("button", { name: /Generate screening answers/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/screening/generate"))).toBe(true));
    const body = calls.find((c) => c.method === "POST")!.body;
    expect(body).toMatchObject({ job_id: "j1", allow_unreviewed: false, length: "medium", candidate_inputs: { notice_period: "30 days" } });
    expect(Object.keys(body.candidate_inputs)).toEqual(["notice_period"]);
  });

  it("a document that FAILED the fact check cannot be approved and says why", async () => {
    mockFetch({ ...BASE, "/api/documents": (url: string) => (url.includes("document_type=cover_letter") ? [{ document_id: "d1" }] : []), "/api/documents/d1": LETTER({ status: "failed", claims_checked: 3, issues: [{ severity: "error", field: "paragraphs.0", message: "Unsupported claim" }] }),
      "/api/documents/d1/versions": [] });
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Cover letter" }));
    expect(await screen.findByText(/Must fix before approval \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Unsupported claim/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approve this version/ })).toBeDisabled();
    expect(screen.getByText(/Fix the errors above/)).toBeInTheDocument();
    expect(screen.getByText(/carry a visible DRAFT marker/)).toBeInTheDocument();
  });

  it("warnings require an explicit acknowledgement, which is sent with the approval", async () => {
    const calls = mockFetch({ ...BASE, "/api/documents": (url: string) => (url.includes("document_type=cover_letter") ? [{ document_id: "d1" }] : []), "/api/documents/d1": LETTER({ status: "passed_with_warnings", claims_checked: 3, issues: [{ severity: "warning", field: "x", message: "Check this" }] }),
      "/api/documents/d1/versions": [], "POST /api/documents/d1/approve": { ok: true } });
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Cover letter" }));
    const approve = await screen.findByRole("button", { name: /Approve this version/ });
    expect(approve).toBeDisabled();
    await userEvent.click(screen.getByLabelText("I have read the warnings and the document is accurate"));
    expect(approve).toBeEnabled();
    await userEvent.click(approve);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/documents/d1/approve")).toBe(true));
    expect(calls.find((c) => c.url === "/api/documents/d1/approve")!.body).toEqual({ acknowledge_warnings: true });
  });

  it("saving an edit PUTs new content (a new version server-side) and an approved document shows as approved", async () => {
    const calls = mockFetch({ ...BASE, "/api/documents": (url: string) => (url.includes("document_type=cover_letter") ? [{ document_id: "d1" }] : []), "/api/documents/d1": LETTER({ status: "passed", claims_checked: 2, issues: [] }, "approved"),
      "/api/documents/d1/versions": [{ document_id: "d1", version: 2, generated_at: "2026-11-13T10:00:00", generator: "deterministic", validation_status: "passed", review_status: "approved", exports: [] }],
      "PUT /api/documents/d1": { ok: true } });
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Cover letter" }));
    expect(await screen.findByText(/Approved: this version is ready/)).toBeInTheDocument();
    const box = screen.getByLabelText("Paragraph 1");
    await userEvent.clear(box);
    await userEvent.type(box, "I build and ship models.");
    await userEvent.click(screen.getByRole("button", { name: /Save changes/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body.content.paragraphs).toEqual([{ text: "I build and ship models.", sources: [] }]);
    expect(within(screen.getByText(/Version history/).closest("details")!).getByText("approved")).toBeInTheDocument();
  });
});
