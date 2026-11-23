import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FlashProvider } from "../components/ui";
import ResumeManager from "./ResumeManager";

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
const view = () => render(<MemoryRouter><FlashProvider><ResumeManager /></FlashProvider></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());

const resume = { resume_id: "r1", original_filename: "jane_cv.pdf", file_type: "pdf", uploaded_at: "2026-11-01T10:00:00+00:00", parsing_status: "completed", parsing_error: null, review_status: "pending_review", profile_version: 2, is_active: true };
const profile = {
  parsing_status: "completed", review_status: "pending_review", profile_version: 2, updated_at: "2026-11-02T09:30:00+00:00", extraction_method: "rule_based",
  review_flags: [{ field: "contact.phone", reason: "derived" }], missing_fields: ["summary.current_job_title"], validation_warnings: [{ severity: "warning", field: "contact.email", message: "looks odd" }],
  contact: { full_name: "Jane Sample", email: "jane@example.com", phone: null, location: "Pune", linkedin_url: null, github_url: null, portfolio_url: null },
  summary: { professional_summary: "ML engineer", current_job_title: null, total_experience_years: null, primary_domains: [], career_objective: null },
  technical_skills: { programming_languages: ["Python"] }, work_experience: [{ company_name: "Acme", job_title: "Engineer", start_date: "2020-01", is_current: true, responsibilities: ["Built models"] }],
  projects: [], education: [], certifications: [], achievements: [],
};
const base: Handler = (url, init) => {
  if (url === "/api/resumes" && isGet(init)) return json([resume]);
  if (url === "/api/resumes/r1/profile" && isGet(init)) return json(profile);
  if (url === "/api/resumes/r1/profile/versions") return json([]);
  if (url.startsWith("/api/documents?")) return json([]);
  return undefined;
};

describe("ResumeManager", () => {
  it("shows a loading state while the list is fetched", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    view();
    expect(screen.getAllByRole("progressbar").length).toBeGreaterThan(0);
  });

  it("shows a clear error (no internals) when the backend fails", async () => {
    mockFetch((url) => (url === "/api/resumes" ? json({ detail: "Database unavailable" }, 503) : undefined));
    view();
    expect(await screen.findByText(/Database unavailable \(HTTP 503\)/)).toBeInTheDocument();
  });

  it("shows an empty state when nothing is uploaded", async () => {
    mockFetch((url) => (url === "/api/resumes" ? json([]) : undefined));
    view();
    expect(await screen.findByText(/No resumes uploaded yet\./)).toBeInTheDocument();
  });

  it("shows the resume, review status, editor values and empty sub-sections", async () => {
    mockFetch(base);
    view();
    expect(await screen.findByText("3. Review status")).toBeInTheDocument();
    expect(screen.getAllByText("jane_cv.pdf").length).toBeGreaterThan(0);
    expect(screen.getByDisplayValue("Jane Sample")).toBeInTheDocument();
    expect(screen.getByText(/Fields needing review \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Missing information \(1\)/)).toBeInTheDocument();
    expect(await screen.findByText("Only the current profile version exists.")).toBeInTheDocument();
    expect(await screen.findByText(/No job-specific resumes generated yet/)).toBeInTheDocument();
    expect(screen.getByText(/Confidence score: not assigned/)).toBeInTheDocument();     // no artificial scores
  });

  it("saves exactly what was edited, keeping blanks blank", async () => {
    const fn = mockFetch((url, init) => (url === "/api/resumes/r1/profile" && init?.method === "PUT" ? json({ profile_version: 3, review_status: "pending_review" }) : undefined), base);
    const user = userEvent.setup();
    view();
    const name = await screen.findByDisplayValue("Jane Sample");
    await user.clear(name);
    await user.type(name, "Jane Q Sample");
    await user.click(screen.getByRole("button", { name: /Save changes/ }));
    await waitFor(() => expect(fn.mock.calls.some(([, i]) => (i as RequestInit | undefined)?.method === "PUT")).toBe(true));
    const put = fn.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === "PUT")!;
    const body = JSON.parse((put[1] as RequestInit).body as string);
    expect(body.contact.full_name).toBe("Jane Q Sample");
    expect(body.contact.phone).toBe("");                              // blank stays blank (the API stores None); nothing is invented
    expect(body.summary.total_experience_years).toBeNull();
    expect(body.review_status).toBe("pending_review");
    expect(body.work_experience[0].company_name).toBe("Acme");
  });

  it("shows the API's message when a save is rejected and does not claim success", async () => {
    mockFetch((url, init) => (url === "/api/resumes/r1/profile" && init?.method === "PUT" ? json({ detail: [{ loc: ["body", "contact", "email"], msg: "not an email" }] }, 422) : undefined), base);
    const user = userEvent.setup();
    view();
    await user.click(await screen.findByRole("button", { name: /Save changes/ }));
    expect(await screen.findByText(/Not saved: contact.email: not an email \(HTTP 422\)/)).toBeInTheDocument();
  });

  it("Cancel discards unsaved edits", async () => {
    mockFetch(base);
    const user = userEvent.setup();
    view();
    const name = await screen.findByDisplayValue("Jane Sample");
    await user.type(name, " EDITED");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByDisplayValue("Jane Sample")).toBeInTheDocument();
  });

  it("rejects a non-PDF/DOCX file before uploading anything", async () => {
    const fn = mockFetch(base);
    const user = userEvent.setup({ applyAccept: false });
    view();
    await user.upload(await screen.findByLabelText("Master resume (PDF or DOCX)"), new File(["x"], "evil.exe"));
    expect(await screen.findByText("Only PDF or DOCX files can be uploaded.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Upload & parse/ })).toBeDisabled();
    expect(fn.mock.calls.some(([, i]) => (i as RequestInit | undefined)?.method === "POST")).toBe(false);
  });

  it("uploads a valid file as multipart form data and reports a duplicate", async () => {
    const fn = mockFetch((url, init) => (url === "/api/resumes/upload" && init?.method === "POST" ? json({ duplicate: true, message: "You already uploaded this file.", resume }) : undefined), base);
    const user = userEvent.setup();
    view();
    await user.upload(await screen.findByLabelText("Master resume (PDF or DOCX)"), new File(["%PDF-1.4"], "cv.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: /Upload & parse/ }));
    expect(await screen.findByText("You already uploaded this file.")).toBeInTheDocument();
    const post = fn.mock.calls.find(([u]) => u === "/api/resumes/upload")!;
    expect((post[1] as RequestInit).body).toBeInstanceOf(FormData);
  });

  it("delete needs the tick AND a second confirmation before anything is sent", async () => {
    const fn = mockFetch((url, init) => (url === "/api/resumes/r1" && init?.method === "DELETE" ? json(null) : undefined), base);
    const user = userEvent.setup();
    view();
    const del = await screen.findByRole("button", { name: /Delete$/ });
    expect(del).toBeDisabled();
    await user.click(screen.getByLabelText(/permanently delete this resume/));
    await user.click(del);
    expect(fn.mock.calls.some(([, i]) => (i as RequestInit | undefined)?.method === "DELETE")).toBe(false);   // only asked so far
    const dialog = screen.getByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Yes, confirm" }));
    await waitFor(() => expect(fn.mock.calls.some(([, i]) => (i as RequestInit | undefined)?.method === "DELETE")).toBe(true));
  });

  it("a resume whose parsing did not complete shows the status and no editor", async () => {
    mockFetch((url) => (url === "/api/resumes" ? json([{ ...resume, parsing_status: "needs_ocr", parsing_error: "scanned PDF" }]) : undefined));
    view();
    expect(await screen.findByText(/scanned PDF/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save changes/ })).not.toBeInTheDocument();
  });
});
