import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";
import { RejectionInbox } from "./RejectionInbox";

const calls: string[] = [];
function mock(ov: any) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push(`${init?.method ?? "GET"} ${url.pathname} ${init?.body ?? ""} ${(init?.headers as any)?.["X-Admin-Action"] ?? ""}`);
    const body = url.pathname === "/api/applications/rejections" ? ov : url.pathname === "/api/applications/page" ? { total: 0, items: [] } : { ok: true };
    return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  }));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); calls.length = 0; });

const CANDS = [{ application_id: "b1", company_name: "Beta Labs", job_title: "Data Scientist", state: "VERIFIED" }, { application_id: "b2", company_name: "Beta Labs", job_title: "ML Ops Engineer", state: "VERIFIED" }];
const OV = {
  connected: true, lookback_days: 30, last_check: { status: "ok", at: "2026-11-26T08:00:00", read: 2, auto_marked: 1, needs_confirmation: 1, unmatched: 0 }, rejected_total: 1,
  needs_confirmation: [{ signal_id: "s1", company: "Beta Labs", role: null, phrase: "regret to inform", sender_domain: "greenhouse.io", received_at: "2026-11-24T09:30:00", reason: "the e-mail names no role", candidates: CANDS }],
  unmatched: [], recent: [{ signal_id: "s2", application_id: "a1", application: { application_id: "a1", company_name: "Acme", job_title: "ML Engineer" }, phrase: "not moving forward", sender_domain: "linkedin.com", received_at: "2026-11-24T09:30:00", decided_by: "email_check" }],
};

describe("RejectionInbox", () => {
  it("asks which application an unsure e-mail is about and confirms the chosen one", async () => {
    mock(OV);
    const changed = vi.fn();
    render(<RejectionInbox onChanged={changed} />);
    expect(await screen.findByText(/Beta Labs — role not named/)).toBeInTheDocument();
    expect(screen.getByText(/1 marked automatically/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox"), "b2");
    await userEvent.click(screen.getByRole("button", { name: /Yes, mark it rejected/ }));
    expect(calls.some((c) => c.startsWith("POST /api/applications/rejections/s1/confirm") && c.includes('"application_id":"b2"'))).toBe(true);
    expect(changed).toHaveBeenCalled();
  });

  it("undoes an automatic mark and checks mail as an admin action", async () => {
    mock(OV);
    render(<RejectionInbox onChanged={() => {}} />);
    await userEvent.click(await screen.findByText(/Marked from e-mail \(1\)/));
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(calls.some((c) => c.startsWith("POST /api/applications/a1/employer-rejected/undo"))).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: /Check mail now/ }));
    expect(calls.some((c) => c.startsWith("POST /api/applications/rejections/check") && c.trim().endsWith("1"))).toBe(true);
  });

  it("explains when Gmail is not connected", async () => {
    mock({ ...OV, connected: false, needs_confirmation: [], recent: [] });
    render(<RejectionInbox onChanged={() => {}} />);
    expect(await screen.findByText(/Gmail is not connected/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Check mail now/ })).toBeNull();
  });

  it("adds outside applications to Applied Jobs as rejected, one or all", async () => {
    const un = [
      { signal_id: "u1", company: "Insight Global", role: "AI Prompt Engineer", phrase: "not moving forward", sender_domain: "linkedin.com", received_at: "2026-11-20T09:30:00", reason: "no application at this company" },
      { signal_id: "u2", company: "PhonePe", role: "ML Engineer", phrase: "not moving forward", sender_domain: "linkedin.com", received_at: "2026-11-21T09:30:00", reason: "no application at this company" },
      { signal_id: "u3", company: "Somewhere", role: null, phrase: "regret to inform", sender_domain: "somewhere.com", received_at: "2026-11-21T09:30:00", reason: "no application at this company" },
    ];
    mock({ ...OV, needs_confirmation: [], recent: [], unmatched: un });
    render(<RejectionInbox onChanged={() => {}} />);
    await userEvent.click(await screen.findByText(/not matched to any application \(3\)/));
    expect(screen.getAllByRole("button", { name: "➕ Add to Applied Jobs" })).toHaveLength(2);     // the e-mail without a role cannot be added
    await userEvent.click(screen.getAllByRole("button", { name: "➕ Add to Applied Jobs" })[0]);
    expect(calls.some((c) => c.startsWith("POST /api/applications/rejections/u1/add-as-applied"))).toBe(true);
    calls.length = 0;
    await userEvent.click(screen.getByRole("button", { name: /Add all 2 to Applied Jobs/ }));
    expect(calls.filter((c) => c.includes("/add-as-applied")).map((c) => c.split(" ")[1])).toEqual(
      ["/api/applications/rejections/u1/add-as-applied", "/api/applications/rejections/u2/add-as-applied"]);
  });
});
