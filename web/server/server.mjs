// AI JobPilot web server: serves the built React app and proxies /api and /health to the FastAPI backend.
//
// Why a proxy: ADMIN_TOKEN must never reach the browser. The browser marks an operational/config call with "X-Admin-Action: 1"; THIS server (and only this
// server) then adds the real "X-Admin-Token". Any X-Admin-Token sent by a browser is removed. The browser is same-origin, so the backend needs no CORS.
import express from "express";
import { createProxyMiddleware } from "http-proxy-middleware";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadDotEnv } from "./env.mjs";

export function createApp({ backend, adminToken, staticDir }) {
  const app = express();
  app.disable("x-powered-by");
  app.use((_req, res, next) => {
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("X-Frame-Options", "DENY");
    res.setHeader("Referrer-Policy", "no-referrer");
    res.setHeader("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'");
    next();
  });

  // What the UI may know about this server. NEVER the token itself.
  app.get("/web/config", (_req, res) => res.json({ adminEnabled: Boolean(adminToken), backend: "proxied" }));

  const proxy = createProxyMiddleware({
    target: backend,
    changeOrigin: false,
    on: {
      proxyReq: (proxyReq, req) => {
        proxyReq.removeHeader("x-admin-token");                     // a browser can never choose the token
        proxyReq.removeHeader("cookie");
        if (adminToken && req.headers["x-admin-action"] === "1") proxyReq.setHeader("X-Admin-Token", adminToken);
        proxyReq.removeHeader("x-admin-action");
      },
      error: (_err, _req, res) => {
        if (res && typeof res.writeHead === "function" && !res.headersSent) {
          res.writeHead(502, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ detail: "Backend unreachable" }));
        }
      },
    },
  });
  app.use("/api", (req, res, next) => { req.url = req.originalUrl; proxy(req, res, next); });
  app.use("/health", (req, res, next) => { req.url = req.originalUrl; proxy(req, res, next); });

  if (staticDir) {
    app.use(express.static(staticDir, { index: false, maxAge: "1h" }));
    app.get("*", (_req, res) => res.sendFile(path.join(staticDir, "index.html")));     // single-page app: any other path -> the app
  }
  return app;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const here = path.dirname(fileURLToPath(import.meta.url));
  if (process.env.JOBPILOT_ENV_FILE !== "none") loadDotEnv(process.env.JOBPILOT_ENV_FILE || path.join(here, "..", "..", ".env"));  // JOBPILOT_ENV_FILE=none (tests) = read no file               // the project's .env (ADMIN_TOKEN, BACKEND_BASE_URL, WEB_PORT, WEB_HOST only); real environment variables win
  const port = Number(process.env.WEB_PORT ?? 8502);
  const host = process.env.WEB_HOST ?? "127.0.0.1";
  const backend = process.env.BACKEND_BASE_URL ?? "http://127.0.0.1:8000";
  const dist = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "dist");
  createApp({ backend, adminToken: process.env.ADMIN_TOKEN || "", staticDir: dist }).listen(port, host, () =>
    console.log(`AI JobPilot web on http://${host}:${port} -> backend ${backend} (admin controls ${process.env.ADMIN_TOKEN ? "enabled" : "read-only"})`));
}
