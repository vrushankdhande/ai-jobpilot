import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NaukriBulkPaste } from "./NaukriTools";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => vi.unstubAllGlobals());

const PAGE = "AI Engineer\nAcme Analytics\n4.1\n5-10 Yrs\nNot disclosed\nPune\nBuild LLM apps with Python and PyTorch.\n1 Day Ago\nSave\n" +
  "Data Scientist\nBeta Labs\n2-5 Yrs\nMumbai\nModels in production.\nJust Now\nSave";

describe("NaukriBulkPaste", () => {
  it("splits the pasted page, imports only the ticked jobs as Naukri, never contacts Naukri, and reports duplicates", async () => {
    fetchMock.mockResolvedValueOnce(json(201, { action: "insert", match: { match_score: 71, eligibility: "PASS" } }));
    const done = vi.fn();
    render(<NaukriBulkPaste onDone={done} />);
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("Paste the copied Naukri results page"));
    await user.paste(PAGE);
    expect(screen.getByText(/2/, { selector: "b" })).toBeInTheDocument();
    await user.click(screen.getByLabelText("Import Data Scientist at Beta Labs"));          // untick the second job
    await user.click(screen.getByRole("button", { name: "Import 1 selected job(s)" }));
    await waitFor(() => expect(screen.getByText(/1 new job\(s\) imported, 0 already known/)).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/jobs/import");
    const body = JSON.parse(init.body);
    expect(body).toMatchObject({ url: null, source: "naukri", job: { job_title: "AI Engineer", company_name: "Acme Analytics", location: "Pune", experience_requirement: "5-10 Yrs" } });
    expect(body.job.job_description).toContain("PyTorch");
    expect(screen.getByText(/Scored 71% · eligibility PASS/)).toBeInTheDocument();
    expect(done).toHaveBeenCalled();
  });

  it("uses a pasted Naukri job link (and then no source hint) and shows a server error per job", async () => {
    fetchMock.mockResolvedValueOnce(json(422, { detail: "job_description: too short" }));
    render(<NaukriBulkPaste onDone={() => {}} />);
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("Paste the copied Naukri results page"));
    await user.paste(PAGE);
    await user.click(screen.getByLabelText("Import Data Scientist at Beta Labs"));
    await user.type(screen.getByLabelText("Link for AI Engineer"), "https://www.naukri.com/job-listings-ai-engineer-acme-pune-5-to-10-years-211125012345");
    await user.click(screen.getByRole("button", { name: "Import 1 selected job(s)" }));
    await waitFor(() => expect(screen.getByText(/too short/)).toBeInTheDocument());
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.url).toContain("naukri.com/job-listings-");
    expect(body.source).toBeUndefined();
    expect(screen.getByText(/0 new job\(s\) imported, 0 already known \(not added twice\), 1 failed/)).toBeInTheDocument();
  });
});

describe("NaukriBulkPaste links", () => {
  it("fills each job's link from the copied page and imports with it (no source hint needed)", async () => {
    const { fireEvent } = await import("@testing-library/react");
    fetchMock.mockResolvedValue(json(201, { action: "insert", match: null }));
    render(<NaukriBulkPaste onDone={() => {}} />);
    const box = screen.getByLabelText("Paste the copied Naukri results page");
    const html = `<a href="https://www.naukri.com/job-listings-ai-engineer-acme-pune-5-to-10-years-211125012345?src=x">AI Engineer</a>`;
    fireEvent.paste(box, { clipboardData: { getData: (t: string) => (t === "text/html" ? html : PAGE) } });
    fireEvent.change(box, { target: { value: PAGE } });
    expect(screen.getByText(/1 job link\(s\) found in what you copied/)).toBeInTheDocument();
    const user = userEvent.setup();
    expect(screen.getByLabelText("Link for AI Engineer")).toHaveValue("https://www.naukri.com/job-listings-ai-engineer-acme-pune-5-to-10-years-211125012345");
    expect(screen.getByLabelText("Link for Data Scientist")).toHaveValue("");
    await user.click(screen.getByLabelText("Import Data Scientist at Beta Labs"));
    await user.click(screen.getByRole("button", { name: "Import 1 selected job(s)" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.url).toBe("https://www.naukri.com/job-listings-ai-engineer-acme-pune-5-to-10-years-211125012345");
    expect(body.source).toBeUndefined();
  });
});
