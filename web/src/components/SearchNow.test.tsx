import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SearchNow from "./SearchNow";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const SOURCES = [{ source: "career_pages", automated_discovery: true }, { source: "linkedin", automated_discovery: false }];
let calls: Array<{ url: string; body?: any }>;

function setup(routes: Record<string, () => Response>) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: any, init?: any) => {
    const u = String(url);
    calls.push({ url: u, body: init?.body ? JSON.parse(init.body) : undefined });
    const key = Object.keys(routes).sort((a, b) => b.length - a.length).find((k) => u.includes(k));
    return key ? routes[key]() : json(404, { detail: "nope" });
  }));
}
afterEach(() => vi.unstubAllGlobals());

describe("SearchNow (one-time search)", () => {
  beforeEach(() => setup({ "/api/jobs/sources": () => json(200, SOURCES) }));

  it("runs one search on the permitted sources only, with the typed keywords, and shows the result", async () => {
    setup({
      "/api/jobs/sources": () => json(200, SOURCES),
      "/api/jobs/discover": () => json(200, { run_id: "r1", status: "running" }),
      "/api/jobs/discovery/status": () => json(200, { status: "completed", totals: { new: 4, fetched: 30, duplicates: 2 } }),
    });
    const onDone = vi.fn();
    render(<MemoryRouter><SearchNow onDone={onDone} /></MemoryRouter>);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Job title / keywords (optional)"), "ML engineer");
    const btn = screen.getByRole("button", { name: "Search now" });
    await waitFor(() => expect(btn).toBeEnabled());
    await user.click(btn);
    await screen.findByText(/Search finished/);
    const post = calls.find((c) => c.url.includes("/api/jobs/discover") && c.body)!;
    expect(post.body.sources).toEqual(["career_pages"]);
    expect(post.body.titles).toEqual(["ML engineer"]);
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(onDone).toHaveBeenCalled();
  });

  it("shows the server's reason when there is nothing to run", async () => {
    setup({
      "/api/jobs/sources": () => json(200, SOURCES),
      "/api/jobs/discover": () => json(200, { status: "nothing_to_run", message: "No permitted source." }),
    });
    render(<MemoryRouter><SearchNow /></MemoryRouter>);
    const user = userEvent.setup();
    const btn = screen.getByRole("button", { name: "Search now" });
    await waitFor(() => expect(btn).toBeEnabled());
    await user.click(btn);
    expect(await screen.findByText("No permitted source.")).toBeInTheDocument();
  });
});
