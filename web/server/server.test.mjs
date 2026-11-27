// @vitest-environment node
// The proxy is a SECURITY component: it must keep ADMIN_TOKEN on the server. Tested against a real fake backend over real HTTP.
import http from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createApp } from "./server.mjs";

const TOKEN = "t".repeat(32);
let backend, web, seen, backendUrl, webUrl, webNoToken, webNoTokenUrl;

function listen(server) { return new Promise((res) => server.listen(0, "127.0.0.1", () => res(`http://127.0.0.1:${server.address().port}`))); }

beforeAll(async () => {
  backend = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      seen = { method: req.method, url: req.url, headers: req.headers, body };
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ ok: true }));
    });
  });
  backendUrl = await listen(backend);
  web = http.createServer(createApp({ backend: backendUrl, adminToken: TOKEN, staticDir: null }));
  webUrl = await listen(web);
  webNoToken = http.createServer(createApp({ backend: backendUrl, adminToken: "", staticDir: null }));
  webNoTokenUrl = await listen(webNoToken);
});
afterAll(() => { backend.close(); web.close(); webNoToken.close(); });

describe("web proxy", () => {
  it("never reveals the token in /web/config", async () => {
    const r = await fetch(`${webUrl}/web/config`);
    const text = await r.text();
    expect(JSON.parse(text)).toEqual({ adminEnabled: true, backend: "proxied" });
    expect(text).not.toContain(TOKEN);
  });

  it("reports read-only when no token is configured", async () => {
    expect((await (await fetch(`${webNoTokenUrl}/web/config`)).json()).adminEnabled).toBe(false);
  });

  it("forwards /api calls with the original path and query", async () => {
    await fetch(`${webUrl}/api/jobs?limit=5&status=new`);
    expect(seen.url).toBe("/api/jobs?limit=5&status=new");
    expect(seen.method).toBe("GET");
  });

  it("does NOT add the token to ordinary calls", async () => {
    await fetch(`${webUrl}/api/jobs`);
    expect(seen.headers["x-admin-token"]).toBeUndefined();
  });

  it("adds the token only when the browser marks the call as an admin action, and never passes the marker on", async () => {
    await fetch(`${webUrl}/api/scheduler/start`, { method: "POST", headers: { "X-Admin-Action": "1" } });
    expect(seen.headers["x-admin-token"]).toBe(TOKEN);
    expect(seen.headers["x-admin-action"]).toBeUndefined();
  });

  it("ignores a token supplied by the browser (it cannot choose or probe the token)", async () => {
    await fetch(`${webUrl}/api/scheduler/start`, { method: "POST", headers: { "X-Admin-Token": "attacker-guess" } });
    expect(seen.headers["x-admin-token"]).toBeUndefined();
    await fetch(`${webUrl}/api/scheduler/start`, { method: "POST", headers: { "X-Admin-Token": "attacker-guess", "X-Admin-Action": "1" } });
    expect(seen.headers["x-admin-token"]).toBe(TOKEN);                    // the server's token replaced the browser's value
  });

  it("sends nothing admin-related when the server has no token configured", async () => {
    await fetch(`${webNoTokenUrl}/api/scheduler/start`, { method: "POST", headers: { "X-Admin-Action": "1", "X-Admin-Token": "x" } });
    expect(seen.headers["x-admin-token"]).toBeUndefined();
  });

  it("does not forward cookies", async () => {
    await fetch(`${webUrl}/api/jobs`, { headers: { Cookie: "session=secret" } });
    expect(seen.headers.cookie).toBeUndefined();
  });

  it("forwards ONLY the sign-in cookie, and only a well-formed one", async () => {
    const sid = "A".repeat(43);
    await fetch(`${webUrl}/api/jobs`, { headers: { Cookie: `other=x; jp_session=${sid}; tracking=y` } });
    expect(seen.headers.cookie).toBe(`jp_session=${sid}`);
    await fetch(`${webUrl}/api/jobs`, { headers: { Cookie: "jp_session=bad value;evil" } });
    expect(seen.headers.cookie).toBeUndefined();
  });

  it("streams request bodies (JSON) unchanged", async () => {
    await fetch(`${webUrl}/api/matching/analyze-jd`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ a: 1 }) });
    expect(seen.body).toBe('{"a":1}');
  });

  it("answers 502 with a plain JSON error when the backend is down", async () => {
    const dead = http.createServer(createApp({ backend: "http://127.0.0.1:1", adminToken: "", staticDir: null }));
    const url = await listen(dead);
    const r = await fetch(`${url}/api/jobs`);
    expect(r.status).toBe(502);
    expect(await r.json()).toEqual({ detail: "Backend unreachable" });
    dead.close();
  });

  it("sets security headers and hides the framework", async () => {
    const r = await fetch(`${webUrl}/web/config`);
    expect(r.headers.get("x-content-type-options")).toBe("nosniff");
    expect(r.headers.get("x-frame-options")).toBe("DENY");
    expect(r.headers.get("content-security-policy")).toContain("default-src 'self'");
    expect(r.headers.get("x-powered-by")).toBeNull();
  });
});
