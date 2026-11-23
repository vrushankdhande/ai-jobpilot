// Reads a few settings from the project's .env so ONE file configures the API and the web server.
// Least privilege: only the listed keys are ever read (never ANTHROPIC_API_KEY, SECRET_KEY, database passwords...), and a variable that is already set
// in the real environment always wins (that is how Docker/CI configure it).
import fs from "node:fs";

export const WEB_KEYS = ["ADMIN_TOKEN", "BACKEND_BASE_URL", "WEB_PORT", "WEB_HOST"];

export function parseDotEnv(text, keys = WEB_KEYS) {
  const out = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 1) continue;
    const key = line.slice(0, eq).trim();
    if (!keys.includes(key)) continue;
    let value = line.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1);
    else value = value.replace(/\s+#.*$/, "");                       // trailing comment on an unquoted value
    out[key] = value;
  }
  return out;
}

/** Fills env[key] from the file for the whitelisted keys that are not already set. Returns the keys it set. Never throws (a missing .env is normal). */
export function loadDotEnv(file, env = process.env, keys = WEB_KEYS) {
  let text;
  try { text = fs.readFileSync(file, "utf8").replace(/^﻿/, ""); } catch { return []; }
  const set = [];
  for (const [k, v] of Object.entries(parseDotEnv(text, keys))) {
    if (env[k] === undefined || env[k] === "") { if (v !== "") { env[k] = v; set.push(k); } }
  }
  return set;
}
