import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LinkedInBulkPaste } from "./LinkedInTools";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => vi.unstubAllGlobals());

const PAGE = [
  "99+ results", "Pune District", "How AI search works",
  "Selected, AI Research Engineer (Remote)AI Research Engineer (Remote)", "Joblet-AI", "India (Remote)", "Posted 10 minutes ago10 minutes ago", "·", "Be an early applicant",
  "Senior ML EngineerSenior ML Engineer", "Avoma", "Pune Division (Remote)", "Posted 1 year ago1 year ago",
  "AI Engineer", "Keywords Studios", "Pune Division (Remote)", "Applied", "·", "Easy Apply",
  "Are these results helpful?", "Next",
  "About the job", "We need 3+ years of experience in Python, LLM evaluation and coding agents for this research engineering role.", "Apply Now!… more", "About the company",
  "Jordan Example", "You: are there openings?",
].join("\n");
const HTML = `<a href="/jobs/view/4300000001/?trk=x">AI Research Engineer (Remote)</a><a href="/jobs/view/4300000003/">AI Engineer</a>`;

function settings() { return json(200, { preferences: { posted_within_days: 90 } }); }

describe("LinkedInBulkPaste", () => {
  it("reads jobs and links from the paste, unticks too-old jobs, imports with exact links or a source hint, and marks LinkedIn 'Applied' jobs", async () => {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.startsWith("/api/settings")) return settings();
      if (url === "/api/jobs/import") {
        const b = JSON.parse(String(init!.body));
        return json(201, { action: "insert", job: { job_id: b.job.company_name === "Joblet-AI" ? "j1" : "j3" },
                           match: b.job.job_description ? { match_score: 72, eligibility: "PASS" } : null });
      }
      if (url === "/api/applications/record-applied") return json(200, { state: "SUBMITTED" });
      return json(404, {});
    });
    const done = vi.fn();
    render(<LinkedInBulkPaste onDone={done} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/settings", expect.anything()));
    const box = screen.getByLabelText("Paste the copied LinkedIn search page");
    fireEvent.paste(box, { clipboardData: { getData: (t: string) => (t === "text/html" ? HTML : PAGE) } });
    fireEvent.change(box, { target: { value: PAGE } });
    expect(screen.getByText(/2 job link\(s\) found/)).toBeInTheDocument();
    const user = userEvent.setup();
    expect(screen.getByText(/2 with an exact link; jobs older than 90 days are unticked/)).toBeInTheDocument();
    expect(screen.getByLabelText("Import Senior ML Engineer at Avoma")).not.toBeChecked();      // posted 1 year ago
    expect(screen.getByText("(with description)")).toBeInTheDocument();
    expect(screen.getAllByText("Open on LinkedIn ↗")).toHaveLength(2);
    expect(screen.getByText("Find on LinkedIn ↗")).toBeInTheDocument();                        // Avoma: no exact link

    await user.click(screen.getByRole("button", { name: "Import 2 selected job(s)" }));
    await waitFor(() => expect(screen.getByText(/2 new job\(s\) imported, 0 already known \(not added twice\), 0 failed, 1 marked as applied/)).toBeInTheDocument());
    const imports = fetchMock.mock.calls.filter(([u]) => u === "/api/jobs/import").map(([, i]) => JSON.parse(String(i.body)));
    expect(imports).toHaveLength(2);
    expect(imports[0]).toMatchObject({ url: "https://www.linkedin.com/jobs/view/4300000001/", job: { job_title: "AI Research Engineer (Remote)", company_name: "Joblet-AI", location: "India", work_mode: "remote" } });
    expect(imports[0].source).toBeUndefined();
    expect(imports[0].job.job_description).toContain("3+ years");
    expect(imports[0].job.job_description).not.toMatch(/Jordan|openings/);
    expect(imports[1]).toMatchObject({ url: "https://www.linkedin.com/jobs/view/4300000003/", job: { company_name: "Keywords Studios", location: "Pune" } });
    const applied = fetchMock.mock.calls.filter(([u]) => u === "/api/applications/record-applied").map(([, i]) => JSON.parse(String(i.body)));
    expect(applied).toEqual([{ job_id: "j3" }]);                                             // only the job LinkedIn showed as Applied, no "verified" note
    expect(screen.getByText(/Not scored yet/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([u]) => String(u).startsWith("/api/"))).toBe(true);    // never contacts LinkedIn
    expect(done).toHaveBeenCalled();
  });

  it("without links in the paste, imports as source linkedin and offers a search link", async () => {
    fetchMock.mockImplementation(async (url: string) => (url.startsWith("/api/settings") ? settings() : json(201, { action: "insert", job: { job_id: "x" }, match: null })));
    render(<LinkedInBulkPaste onDone={() => {}} />);
    const box = screen.getByLabelText("Paste the copied LinkedIn search page");
    fireEvent.change(box, { target: { value: PAGE } });
    expect(screen.getByText(/No job links came with the paste/)).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("Import AI Engineer at Keywords Studios"));
    await user.click(screen.getByRole("button", { name: "Import 1 selected job(s)" }));
    await waitFor(() => expect(screen.getByText(/1 new job\(s\) imported/)).toBeInTheDocument());
    const body = JSON.parse(String(fetchMock.mock.calls.find(([u]) => u === "/api/jobs/import")![1].body));
    expect(body).toMatchObject({ url: null, source: "linkedin", job: { job_title: "AI Research Engineer (Remote)" } });
  });
});
