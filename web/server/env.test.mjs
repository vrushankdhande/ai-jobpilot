// @vitest-environment node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { loadDotEnv, parseDotEnv } from "./env.mjs";

describe("project .env loader", () => {
  it("reads only the whitelisted keys, never secrets", () => {
    const parsed = parseDotEnv("ADMIN_TOKEN=abc123\nANTHROPIC_API_KEY=sk-secret\nSECRET_KEY=zzz\nMONGO_ROOT_PASSWORD=pw\nBACKEND_BASE_URL=http://127.0.0.1:8000\n");
    expect(parsed).toEqual({ ADMIN_TOKEN: "abc123", BACKEND_BASE_URL: "http://127.0.0.1:8000" });
  });
  it("handles comments, quotes, blank lines, CRLF and trailing comments", () => {
    const parsed = parseDotEnv('# c\r\n\r\nADMIN_TOKEN="quoted value"\r\nWEB_PORT=8600  # dev port\r\nWEB_HOST=\'127.0.0.1\'\r\nnot a pair\r\n');
    expect(parsed).toEqual({ ADMIN_TOKEN: "quoted value", WEB_PORT: "8600", WEB_HOST: "127.0.0.1" });
  });
  it("does not override a variable that is already set, and ignores empty values", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "jp-env-"));
    const file = path.join(dir, ".env");
    fs.writeFileSync(file, "﻿ADMIN_TOKEN=from-file\nWEB_PORT=9000\nWEB_HOST=\n");
    const env = { WEB_PORT: "8502" };
    const set = loadDotEnv(file, env);
    expect(env).toEqual({ WEB_PORT: "8502", ADMIN_TOKEN: "from-file" });         // real environment wins; BOM tolerated; empty WEB_HOST not set
    expect(set).toEqual(["ADMIN_TOKEN"]);
  });
  it("a missing .env is fine", () => {
    expect(loadDotEnv(path.join(os.tmpdir(), "definitely-missing", ".env"), {})).toEqual([]);
  });
});
