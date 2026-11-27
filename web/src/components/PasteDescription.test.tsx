import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PasteDescription } from "./PasteDescription";

const JOB = { id: "a".repeat(32), title: "Associate Architect - ML", company: "Quantiphi Analytics", url: "https://www.instahyre.com/job-448472-x/" };
const TEXT = "Requirements: Python, PyTorch, LLMs and MLOps on AWS. ".repeat(6);
function backend(status: number, body: unknown) {
  const calls: Array<{ url: string; body: any }> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    calls.push({ url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null });
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  }));
  return calls;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("PasteDescription", () => {
  it("sends the pasted text for THIS job and shows the score", async () => {
    const calls = backend(200, { job: {}, match: { match_score: 78.5, classification: "Good Match", eligibility: "PASS" } });
    const done = vi.fn();
    render(<PasteDescription job={JOB} onDone={done} onClose={() => {}} />);
    expect(screen.getByRole("link", { name: /Open job/ })).toHaveAttribute("href", JOB.url);
    const save = screen.getByRole("button", { name: "Save & score" });
    expect(save).toBeDisabled();                                                     // nothing pasted yet
    await userEvent.click(screen.getByLabelText("Job description"));
    await userEvent.paste(TEXT);
    await userEvent.click(save);
    expect(await screen.findByText(/Scored: 78.5 \/ 100 · Good Match · eligibility PASS/)).toBeInTheDocument();
    expect(calls[0].url).toBe(`/api/jobs/${JOB.id}/description`);
    expect(calls[0].body).toEqual({ job_description: TEXT.trim() });
    expect(done).toHaveBeenCalled();
  });

  it("keeps the pasted text when the server refuses it", async () => {
    backend(409, { detail: "This job already has a description." });
    render(<PasteDescription job={JOB} onDone={() => {}} onClose={() => {}} />);
    await userEvent.click(screen.getByLabelText("Job description"));
    await userEvent.paste(TEXT);
    await userEvent.click(screen.getByRole("button", { name: "Save & score" }));
    expect(await screen.findByText(/already has a description/)).toBeInTheDocument();
    expect((screen.getByLabelText("Job description") as HTMLTextAreaElement).value).toBe(TEXT);
  });
});
