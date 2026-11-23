import { afterEach, describe, expect, it, vi } from "vitest";
import { api, explain, queryString } from "./api";
import { clampPage, DASH, fmtDt, pageCount, schedulerState, show } from "./format";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
afterEach(() => vi.unstubAllGlobals());

describe("queryString", () => {
  it("drops empty values and encodes the rest", () => {
    expect(queryString({ a: 1, b: "", c: null, d: undefined, e: "x y", f: false })).toBe("?a=1&e=x+y&f=false");
    expect(queryString({})).toBe("");
  });
});

describe("explain", () => {
  it("handles dict, FastAPI validation list and plain string details", () => {
    expect(explain({ message: "Bad", problems: ["p1"] })).toMatchObject({ message: "Bad", problems: ["p1"] });
    expect(explain([{ loc: ["body", "company_name"], msg: "too short" }]).message).toBe("company_name: too short");
    expect(explain("nope").message).toBe("nope");
    expect(explain(undefined).message).toBe("Request failed");
  });
});

describe("api client", () => {
  it("returns data on success and sends no admin marker by default", async () => {
    const f = vi.fn().mockResolvedValue(json(200, { x: 1 }));
    vi.stubGlobal("fetch", f);
    const r = await api.get("/api/jobs", { params: { limit: 2 } });
    expect(r).toMatchObject({ ok: true, data: { x: 1 } });
    expect(f.mock.calls[0][0]).toBe("/api/jobs?limit=2");
    expect(f.mock.calls[0][1].headers["X-Admin-Action"]).toBeUndefined();
  });

  it("marks admin calls with X-Admin-Action and NEVER sets X-Admin-Token", async () => {
    const f = vi.fn().mockResolvedValue(json(200, {}));
    vi.stubGlobal("fetch", f);
    await api.post("/api/scheduler/start", { admin: true });
    const headers = f.mock.calls[0][1].headers;
    expect(headers["X-Admin-Action"]).toBe("1");
    expect(Object.keys(headers).map((h) => h.toLowerCase())).not.toContain("x-admin-token");
  });

  it("turns HTTP errors into a readable message with the status, without throwing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(409, { detail: { message: "Already submitted", problems: ["a"] } })));
    const r = await api.post("/api/applications/1/submit", { admin: true });
    expect(r).toMatchObject({ ok: false, status: 409, error: "Already submitted (HTTP 409)", problems: ["a"] });
  });

  it("does not leak server internals when the error body is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Traceback (most recent call last) ...", { status: 500 })));
    const r = await api.get("/api/x");
    expect(r.error).toBe("Request failed (HTTP 500)");
    expect(r.error).not.toContain("Traceback");
  });

  it("reports an unreachable backend instead of throwing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    expect(await api.get("/api/x")).toMatchObject({ ok: false, error: "Backend unreachable. Is the API running?" });
  });

  it("sends JSON bodies with a content type and never sends credentials", async () => {
    const f = vi.fn().mockResolvedValue(json(201, { id: 1 }));
    vi.stubGlobal("fetch", f);
    await api.post("/api/interviews", { json: { company_name: "Co" } });
    expect(f.mock.calls[0][1].body).toBe('{"company_name":"Co"}');
    expect(f.mock.calls[0][1].headers["Content-Type"]).toBe("application/json");
    expect(f.mock.calls[0][1].credentials).toBe("omit");
  });
});

describe("format helpers", () => {
  it("paginates safely", () => {
    expect(pageCount(0, 10)).toBe(1);
    expect(pageCount(21, 10)).toBe(3);
    expect(clampPage(9, 3)).toBe(3);
    expect(clampPage(0, 3)).toBe(1);
  });
  it("shows a dash instead of inventing values", () => {
    expect(fmtDt(null)).toBe(DASH);
    expect(fmtDt("garbage-date")).toBe("garbage-date");
    expect(show(undefined)).toBe(DASH);
    expect(show(0)).toBe("0");
  });
  it("formats dates", () => {
    expect(fmtDt("2026-11-13T05:07:00", true)).toBe("13 Nov 2026 05:07");
    expect(fmtDt("2026-11-13T05:07:00", false)).toBe("13 Nov 2026");
  });
  it("names the scheduler state", () => {
    expect(schedulerState({ enabled: true, paused: true })).toContain("Paused");
    expect(schedulerState({ enabled: true })).toContain("Running");
    expect(schedulerState({ enabled: false })).toContain("Stopped");
  });
});
