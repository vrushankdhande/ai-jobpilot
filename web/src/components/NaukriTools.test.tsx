import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NaukriQuickImport, NaukriSearch } from "./NaukriTools";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn().mockResolvedValue(json(201, { action: "insert" })); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => vi.unstubAllGlobals());

const LONG = "AI Engineer\nAcme Analytics · Pune\nWe are hiring an AI engineer to build and deploy computer vision models in Python.";
const LINK = "https://www.naukri.com/job-listings-ai-engineer-acme-analytics-pune-2-to-5-years-170924012345";

describe("NaukriSearch", () => {
  it("has no usable link until keywords are entered", () => {
    render(<NaukriSearch />);
    expect(screen.queryByRole("link", { name: /Open Naukri search/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Open Naukri search/ })).toBeDisabled();
  });

  it("builds a Naukri link that opens in a new tab without leaking the opener, and requests nothing", async () => {
    render(<NaukriSearch />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Naukri keywords"), "AI engineer");
    await user.type(screen.getByLabelText("Naukri location"), "Pune");
    await user.type(screen.getByLabelText("Naukri experience (years)"), "3");
    await user.selectOptions(screen.getByLabelText("Naukri posted within"), "week");
    const a = screen.getByRole("link", { name: /Open Naukri search/ });
    const u = new URL(a.getAttribute("href")!);
    expect(u.origin + u.pathname).toBe("https://www.naukri.com/ai-engineer-jobs-in-pune");
    expect(u.searchParams.get("k")).toBe("AI engineer");
    expect(u.searchParams.get("l")).toBe("Pune");
    expect(u.searchParams.get("experience")).toBe("3");
    expect(u.searchParams.get("jobAge")).toBe("7");
    expect(a).toHaveAttribute("target", "_blank");
    expect(a.getAttribute("rel")).toContain("noopener");
    expect(a.getAttribute("rel")).toContain("noreferrer");
    expect(fetchMock).not.toHaveBeenCalled();                                        // this app never contacts Naukri (or anything) for a search link
  });
});

describe("NaukriQuickImport", () => {
  it("shows the detected job id and stays disabled until everything is valid", async () => {
    render(<NaukriQuickImport onDone={() => {}} />);
    const user = userEvent.setup();
    const button = screen.getByRole("button", { name: "Import Naukri job" });
    expect(button).toBeDisabled();
    await user.type(screen.getByLabelText("Naukri job link"), LINK);
    expect(screen.getByRole("status")).toHaveTextContent("170924012345");
    expect(button).toBeDisabled();
    await user.click(screen.getByLabelText(/Paste the Naukri job text/));
    await user.paste(LONG);
    expect(screen.getByLabelText(/Naukri job title/)).toHaveValue("AI Engineer");
    expect(button).toBeDisabled();                                                   // company is never guessed
    await user.type(screen.getByLabelText("Naukri company *"), "Acme Analytics");
    expect(button).toBeEnabled();
  });

  it("rejects links that are not Naukri job links", async () => {
    render(<NaukriQuickImport onDone={() => {}} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Naukri job link"), "https://naukri.com.evil.example/job-listings-x-170924012345");
    expect(screen.getByText(/does not look like a Naukri job link/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import Naukri job" })).toBeDisabled();
  });

  it("imports through the normal API with the CLEAN link only, never contacts Naukri, and clears the form", async () => {
    const done = vi.fn();
    render(<NaukriQuickImport onDone={done} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Naukri job link"), `${LINK}?src=jobsearchDesk&sid=SECRET`);
    await user.click(screen.getByLabelText(/Paste the Naukri job text/));
    await user.paste(LONG);
    await user.type(screen.getByLabelText("Naukri company *"), "Acme Analytics");
    await user.click(screen.getByRole("button", { name: "Import Naukri job" }));
    await waitFor(() => expect(screen.getByText("Imported as a new job.")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/jobs/import");
    const body = JSON.parse(init.body);
    expect(body.url).toBe(LINK);
    expect(JSON.stringify(body)).not.toContain("SECRET");                            // tracking parameters are dropped
    expect(body.job).toMatchObject({ job_title: "AI Engineer", company_name: "Acme Analytics" });
    expect(init.headers["X-Admin-Action"]).toBeUndefined();
    expect(done).toHaveBeenCalled();
    expect(screen.getByLabelText("Naukri job link")).toHaveValue("");
  });

  it("keeps what was typed and shows the server's error when the import is rejected", async () => {
    fetchMock.mockResolvedValue(json(422, { detail: "job_description: too short" }));
    render(<NaukriQuickImport onDone={() => {}} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Naukri job link"), LINK);
    await user.click(screen.getByLabelText(/Paste the Naukri job text/));
    await user.paste(LONG);
    await user.type(screen.getByLabelText("Naukri company *"), "Acme Analytics");
    await user.click(screen.getByRole("button", { name: "Import Naukri job" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("too short"));
    expect(screen.getByLabelText("Naukri company *")).toHaveValue("Acme Analytics");
    expect(screen.getByLabelText("Naukri job link")).toHaveValue(LINK);
  });
});
