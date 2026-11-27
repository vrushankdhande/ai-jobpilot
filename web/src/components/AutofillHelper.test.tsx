import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AUTOFILL_MARK } from "../lib/autofill";
import { AutofillHelper } from "./AutofillHelper";

const DATA = { job: { job_id: "j".repeat(32), title: "AI Engineer", company: "Acme" },
  fields: [{ key: "email", label: "E-mail", value: "jane.sample@example.com" }, { key: "first_name", label: "First name", value: "Jane", derived: true }],
  answers: [{ question: "Why Acme?", answer: "Search." }], missing: ["Expected salary"], problems: [] };
const JOB = "a".repeat(32);

function backend(status = 200) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
    calls.push(String(input));
    return new Response(JSON.stringify(status === 200 ? DATA : { detail: "Profile not reviewed" }), { status, headers: { "content-type": "application/json" } });
  }));
  return calls;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("AutofillHelper", () => {
  it("copies this job's data with the marker and offers the bookmark as a javascript: link that does nothing when clicked here", async () => {
    const calls = backend();
    const write = vi.fn(async () => {});
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: write } });
    render(<AutofillHelper jobId={JOB} applyUrl="https://jobs.lever.co/acme/1" />);
    const mark = screen.getByText("⚡ JobPilot Autofill") as HTMLAnchorElement;
    expect(mark.getAttribute("href")).toMatch(/^javascript:/);
    await userEvent.click(mark);                                                   // clicking on JobPilot's own page only explains how to use it
    expect(await screen.findByText(/Drag the button to your bookmarks bar/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "📋 Copy autofill data" }));
    await waitFor(() => expect(write).toHaveBeenCalledTimes(1));
    expect(calls[0]).toContain(`/api/applications/autofill/${JOB}`);
    const text = (write.mock.calls[0] as unknown as [string])[0];
    expect(text.startsWith(AUTOFILL_MARK)).toBe(true);
    expect(JSON.parse(text.slice(AUTOFILL_MARK.length))).toMatchObject({ fields: { email: "jane.sample@example.com", first_name: "Jane" }, derived: ["first_name"],
      answers: [{ question: "Why Acme?", answer: "Search." }] });
    expect(await screen.findByText(/Copied 2 details and 1 screening answer/)).toBeInTheDocument();
    expect(screen.getByText(/Expected salary/)).toBeInTheDocument();             // what is missing is said, not invented
    expect(screen.getByRole("link", { name: /Open the application form/ })).toHaveAttribute("href", "https://jobs.lever.co/acme/1");
  });

  it("shows the data to copy by hand when the browser blocks the clipboard, and shows server errors", async () => {
    backend();
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: vi.fn(async () => { throw new Error("denied"); }) } });
    render(<AutofillHelper jobId={JOB} applyUrl={null} />);
    await userEvent.click(screen.getByRole("button", { name: "📋 Copy autofill data" }));
    expect((await screen.findByLabelText("Autofill data to copy") as HTMLTextAreaElement).value.startsWith(AUTOFILL_MARK)).toBe(true);
    cleanup();
    backend(409);
    render(<AutofillHelper jobId={JOB} applyUrl={null} />);
    await userEvent.click(screen.getByRole("button", { name: "📋 Copy autofill data" }));
    expect(await screen.findByText(/Profile not reviewed/)).toBeInTheDocument();
  });
});
