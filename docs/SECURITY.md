# Security

What protects your data and accounts in AI JobPilot, and what does not. Each control below is enforced in code and
covered by an automated test unless marked otherwise.

## Rules the system never breaks

- **No submission without your approval.** An application is sent only after a backend-recorded approval bound to
  the exact plan (hash). The background worker can only *prepare*; it has no code path to approve or submit
  (a safety test scans for one).
- **No scraping, automated login, CAPTCHA solving or anti-bot evasion.** LinkedIn, Indeed and Naukri are manual
  import only. A bot check or login wall stops the run for that source.
- **No fabricated candidate data.** Documents use verified profile facts only; missing details stay as
  `needs_input`.
- **Fail closed.** An unknown source, an invalid policy value or an unreadable policy file means *disabled*.

## Secrets

- Secrets live only in `.env` (git-ignored, excluded from Docker images). They are never shown or editable in the
  dashboard; Settings shows only "configured / not configured".
- `ADMIN_TOKEN` is required for every state-changing operational call. Empty token = those calls are refused.
- The browser never receives `ADMIN_TOKEN`. It marks a call `X-Admin-Action: 1`; the Node server adds the real
  header and discards any token or cookie sent by a browser. A safety test scans the built bundle for the token
  and its variable name.
- Gmail OAuth credentials, saved browser sessions (cookies only, never passwords) and optional backups are
  Fernet-encrypted with a key derived from `SECRET_KEY`.
- In `APP_ENV=production` the API refuses to start without `ADMIN_TOKEN` (24+ characters) and `SECRET_KEY`.

## Personal data

- Resume files are validated by content, stored under server-generated names in `storage/resumes/`, never
  overwritten and never served by path or URL.
- Logs and application events contain ids, types and sizes only - never resume text, job text or personal details.
- AI is off by default. With it on, each use needs its own consent flag. Matching never sends personal details;
  document wording never sends your name or contact details. Resume extraction does send resume text
  (`AI_EXTRACTION_ENABLED`). All AI output is fact-checked and discarded on failure.
- Everything is stored locally (MongoDB and `storage/`). Nothing is sent anywhere except: the public job-board
  APIs you configured (requests contain no personal data), the optional AI provider, and optional Gmail.

## Network

- Outbound requests go through one guard (`PolicyHttp`): https only, allow-listed hosts, no private or local
  addresses, redirects re-validated on every hop, per-source rate limit and request budget, bounded response size
  (5 MB default; 40 MB for the Greenhouse API; hard ceiling 60 MB).
- A URL typed by a user is stored but never fetched, except through an allow-listed provider API.
- The API limits request body size (HTTP 413) and returns generic 500 errors without internal messages.
- The web server sets a Content-Security-Policy and standard security headers, and binds to `127.0.0.1` by default.

## Application safety

- Automation is closed by default (`APPLICATION_AUTOMATION_ENABLED=false`) and limited to sites allowed in
  `config/application_policies.yaml`. Shipped policy: local test forms only; Greenhouse/Lever/generic need you to
  authorise each site; LinkedIn/Indeed/Naukri/Workday are manual handoff.
- Duplicate submissions are prevented by idempotency keys and unique indexes. A submission with an uncertain
  outcome becomes `VERIFICATION_PENDING` and is never retried automatically.
- `SUBMITTED` / `VERIFIED` are recorded only with evidence from the page.

## Known gaps (not protected today)

- **The dashboard has no login.** Anyone who can reach its port can use it, including operational actions when
  `ADMIN_TOKEN` is set on the server. Keep it on `127.0.0.1`, or put an authenticating reverse proxy in front.
- **Python dependency audit not completed** (see `TESTING_REPORT.md` for the current state).
- **Docker deployment never run**, so its hardening settings are untested.
- The platform policy review was desk research, not legal advice. Confirm a site's terms yourself before
  authorising automation for it.
- MongoDB on a local install has no authentication by default.

## Reporting a problem

This is a personal, single-user project. If you find a weakness, stop the scheduler (Automation -> Stop), keep the
logs (`logs/app.log`), and fix or disable the affected feature before continuing.
