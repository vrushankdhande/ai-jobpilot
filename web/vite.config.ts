/// <reference types="vitest" />
import path from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";
// @ts-expect-error plain JS module shared with the production server
import { loadDotEnv } from "./server/env.mjs";
// @ts-expect-error plain JS module shared with the production server
import { createApp } from "./server/server.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));

// Dev: ONE command. The same proxy as production (server/server.mjs) runs inside Vite, straight to the API (default http://127.0.0.1:8000), so no second
// web server or port is needed and ADMIN_TOKEN (read from the project's .env, or the environment) still never reaches the browser.
function jobpilotProxy(): Plugin {
  return {
    name: "jobpilot-proxy",
    configureServer(server) {
      loadDotEnv(path.join(here, "..", ".env"));
      const app = createApp({ backend: process.env.BACKEND_BASE_URL || "http://127.0.0.1:8000", adminToken: process.env.ADMIN_TOKEN || "", staticDir: null });
      server.middlewares.use((req, res, next) => (/^\/(api|health|web)(\/|\?|$)/.test(req.url ?? "") ? app(req, res, next) : next()));
    },
  };
}

export default defineConfig({
  plugins: [react(), jobpilotProxy()],
  server: { port: 5173 },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"], css: false },
});
