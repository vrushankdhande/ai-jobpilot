import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LinkedInJobsTable, LinkedInSearch } from "./LinkedInTools";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn().mockResolvedValue(json(201, { action: "insert" })); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => vi.unstubAllGlobals());


describe("LinkedInSearch", () => {
  it("has no usable link until keywords are entered", () => {
    render(<LinkedInSearch />);
    expect(screen.queryByRole("link", { name: /Open LinkedIn search/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Open LinkedIn search/ })).toBeDisabled();
  });

  it("builds a LinkedIn link that opens in a new tab without leaking the opener, and requests nothing", async () => {
    render(<LinkedInSearch />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Keywords"), "AI engineer");
    await user.type(screen.getByLabelText("Location"), "Pune");
    await user.click(screen.getByLabelText("remote"));
    await user.selectOptions(screen.getByLabelText("Posted within"), "week");
    const a = screen.getByRole("link", { name: /Open LinkedIn search/ });
    const u = new URL(a.getAttribute("href")!);
    expect(u.origin + u.pathname).toBe("https://www.linkedin.com/jobs/search/");
    expect(u.searchParams.get("keywords")).toBe("AI engineer");
    expect(u.searchParams.get("location")).toBe("Pune");
    expect(u.searchParams.get("f_WT")).toBe("2");
    expect(u.searchParams.get("f_TPR")).toBe("r604800");
    expect(a).toHaveAttribute("target", "_blank");
    expect(a.getAttribute("rel")).toContain("noopener");
    expect(a.getAttribute("rel")).toContain("noreferrer");
    expect(fetchMock).not.toHaveBeenCalled();                                       // this app never contacts LinkedIn (or anything) for a search link
  });
});


describe("profile defaults and the LinkedIn jobs table", () => {
  const sug = { profile_available: true, current_title: "Data Scientist", titles: ["Data Scientist", "ML Engineer"], skills: ["Python"], location: "Pune", experience_years: 5 };
  it("prefills keywords and location from the profile, lets you change them, and offers other titles", async () => {
    render(<LinkedInSearch suggestions={sug} />);
    expect(screen.getByLabelText("Keywords")).toHaveValue("Data Scientist");
    expect(screen.getByLabelText("Location")).toHaveValue("Pune");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "ML Engineer" }));
    expect(screen.getByLabelText("Keywords")).toHaveValue("ML Engineer");
    await user.clear(screen.getByLabelText("Location")); await user.type(screen.getByLabelText("Location"), "Remote");
    expect(new URL(screen.getByRole("link", { name: /Open LinkedIn search/ }).getAttribute("href")!).searchParams.get("location")).toBe("Remote");
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("shows score, eligibility and application status; unscored jobs say so", async () => {
    fetchMock.mockResolvedValue(json(200, { total: 2, items: [
      { job_id: "a".repeat(32), job_title: "AI Engineer", company_name: "Acme", location: "Pune", experience_min: 3, experience_max: null, posted_date: null, discovered_at: "2026-11-20T00:00:00Z", match_score: 71.5, eligibility: "PASS", job_url: "https://www.linkedin.com/jobs/view/4485396197/", application_status: "NOT_STARTED" },
      { job_id: "b".repeat(32), job_title: "Analyst", company_name: "Beta", location: null, experience_min: null, experience_max: null, posted_date: null, discovered_at: "2026-11-20T00:00:00Z", match_score: null, eligibility: null, job_url: null, application_status: "NOT_STARTED" }] }));
    render(<LinkedInJobsTable />);
    await waitFor(() => expect(screen.getByText("71.5%")).toBeInTheDocument());
    expect(screen.getByText("PASS")).toBeInTheDocument();
    expect(screen.getByText("3+ yrs")).toBeInTheDocument();
    expect(screen.getAllByText("Not scored").length).toBe(2);
    expect(screen.getByRole("link", { name: /Open on LinkedIn/ })).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });
});
