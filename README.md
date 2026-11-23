# AI JobPilot

AI-powered job discovery, resume analysis, job matching and application assistant.
**Phase 1 (this state)** is the project foundation: configuration, logging, MongoDB
connectivity, a FastAPI backend with health endpoints, and a React web dashboard.

## Technology stack

Python 3.11 · uv · FastAPI · React (Vite + TypeScript) + Node · MongoDB + PyMongo · Pydantic Settings · logging · pytest · Git

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (`uv --version`)
- Python 3.11 (`uv python install 3.11`)
- MongoDB running locally (default `mongodb://localhost:27017/`) or an Atlas URI
- Git (Windows: `winget install --id Git.Git -e`)

## Setup (PowerShell or Command Prompt)

```bash
uv sync                      # creates .venv with Python 3.11 and installs everything
copy .env.example .env       # (cmd)   |   Copy-Item .env.example .env   (PowerShell)
uv run python scripts/check_environment.py
```

## Configuration

All settings live in `config/settings.py` (Pydantic Settings) and are loaded from `.env`.
See `.env.example` for every variable (`APP_ENV`, `API_PORT`, `BACKEND_BASE_URL`,
`MONGODB_URI`, `MONGODB_DATABASE`, `LOG_LEVEL`, `LOG_FILE`, ...). Never commit `.env`.

## MongoDB

- Local: install MongoDB Community Server and make sure the service is running, then keep
  `MONGODB_URI=mongodb://localhost:27017/`.
- Atlas: set `MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/` and allow your IP in Atlas.

Phase 1 only pings the server; it creates no collections or indexes.

> **Full description of the application** (how it works, every page, sources, scoring, configuration, API, status and limitations): [`docs/APPLICATION_OVERVIEW.md`](docs/APPLICATION_OVERVIEW.md).

## How to run the application

The application is two programs that run side by side: the **API** (Python, port 8000) and the **web dashboard** (Node, port 8502 unless
`WEB_PORT` in `.env` says otherwise). MongoDB must be running first. All commands are PowerShell, run from the project folder.

### 1. First time only

```powershell
uv sync                                  # Python 3.11 environment and packages
Copy-Item .env.example .env              # then open .env and set ADMIN_TOKEN and SECRET_KEY (any long random text)
uv run python scripts/check_environment.py   # confirms Python, packages and the MongoDB connection
winget install OpenJS.NodeJS.LTS         # Node.js 22+ (skip if `node --version` already works)
cd web; npm install; npm run build; cd ..    # install and build the dashboard
```

If `uv sync` stalls while downloading Playwright, use `uv sync --no-install-package playwright` and add `--no-sync` after `uv run` in the commands below.

### 2. Start it (every time)

Open **two** PowerShell windows in the project folder and leave both open.

```powershell
# window 1 - the API
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000

# window 2 - the dashboard
cd web; npm start
```

Then open the dashboard in your browser: **http://127.0.0.1:8502** (or the port in `WEB_PORT`, e.g. http://127.0.0.1:8501).
The Home page shows "Backend: connected" and "MongoDB: connected" when everything is up.

### 3. Use it

1. **Resume Manager** - upload your resume, correct the extracted profile and mark it reviewed. Nothing is matched against an unreviewed profile.
2. **Settings -> Preferences** - your job titles and locations (also notice period and salary; they are never guessed). These decide which jobs are kept.
3. **Job Discovery** - "Search once, now" on Home, or Run discovery here, reads the company career pages listed in `config/career_sources.yaml`.
   LinkedIn, Naukri, Indeed and Instahyre are manual: open the search link, then paste a job's link and text into the quick import. An imported job is scored straight away.
4. **Job Matching** - "G. Match results" lists every scored job with its score, eligibility (PASS / FAIL), source and link. Details opens the full explanation.
5. **Application Queue** - good matches (PASS and at or above `min_match_score` in `config/automation_limits.yaml`) are prepared here. **Nothing is submitted without your approval.**

### 4. Run it automatically every day (optional)

On the **Automation** page press Start (needs `ADMIN_TOKEN` in `.env`), or set `SCHEDULER_AUTOSTART=true` in `.env`. While the API window stays open the scheduler then:
discovers jobs daily at 07:00, scores them, queues the good matches, prepares applications between 09:00 and 21:00 and writes a daily report at 20:00
(times and limits: `config/automation_limits.yaml`, time zone `Asia/Kolkata`). It never approves or submits anything.

### 5. Stop, restart, update

- **Stop**: press `Ctrl+C` in each window.
- **After changing Python code or a `config/*.yaml` file**: stop and start window 1 again (or start the API with `--reload` to restart on every code change).
- **After changing anything in `web/`**: `cd web; npm run build`, then refresh the browser with `Ctrl+F5`. `npm start` does not need a restart.
- **Front-end development**: `cd web; npm run dev` and open **http://localhost:5173** (use `localhost`). It is one command: the same proxy runs inside Vite and talks straight to the API on 8000, so `npm start` is not needed.

The web server (and `npm run dev`) read `ADMIN_TOKEN`, `BACKEND_BASE_URL`, `WEB_PORT` and `WEB_HOST` from the project's `.env` (only those four keys; real environment variables win), so you set `ADMIN_TOKEN` once, in `.env`, for both the API and the dashboard.

### 6. If something does not work

| What you see | What to do |
|---|---|
| Home says "Backend: unreachable" | Window 1 is not running, or it shows an error - start the API again |
| "MongoDB: not connected" | Start the MongoDB service, or check `MONGODB_URI` in `.env` |
| The page looks old after an update | `cd web; npm run build`, then `Ctrl+F5` |
| "Port already in use" | Another copy is still running - close the old window, or change `API_PORT` / `WEB_PORT` in `.env` |
| Start / Run buttons say admin controls are disabled | Set `ADMIN_TOKEN` in `.env` and restart both windows |
| Discovery finds 0 new jobs | Check your titles and locations in Settings, the companies in `config/career_sources.yaml`, and the daily cap (`max_jobs_discovered_per_day`) |

- API docs (Swagger): http://127.0.0.1:8000/docs
- Endpoints: `GET /`, `GET /health`, `GET /health/database`

## Tests

```bash
uv run pytest -v
```

Most database tests use mocks and do **not** prove a real MongoDB connection. The test marked
`integration` pings the configured MongoDB and is skipped if it is unreachable.
`scripts/check_environment.py` also performs a real ping.

## Logs

Written to `logs/app.log` (rotating) and to the console.

## Phase 2 – Resume intelligence

Upload a master resume, extract a structured candidate profile, then review and correct it.

**Use it:** start the API and the web dashboard (see *Run*), open **Resume Manager** in the sidebar,
upload a `.pdf` or `.docx`, review the extracted tabs, edit, and press **Save changes**.
API docs: http://127.0.0.1:8000/docs

### Upload process and supported types
- `.pdf` (text-based) and `.docx`; max size `MAX_UPLOAD_MB` (default 10).
- The file *content* is validated (PDF header / DOCX zip structure), not just the extension or MIME type.
- Files are stored in `storage/resumes/<random-id>.<ext>` and never overwritten; the uploaded filename is
  display-only. Storage paths are never returned by the API and files are not served over HTTP.
- Identical files (same SHA-256) are not stored twice.
- Encrypted, corrupted, empty and **scanned** PDFs are reported as `failed` / `needs_ocr` – never as parsed.
  OCR is an extension point only: assign a `path -> text` callable to `engines.resume.resume_parser.OCR_HANDLER`.

### Extraction pipeline
`validate -> extract text (PyMuPDF / python-docx) -> clean -> detect sections -> rule-based extraction ->
(optional AI fill) -> Pydantic validation -> factuality validation -> MongoDB`.
- Layer 1 (always on, no API key): deterministic rules for contact info, dates, sections, skills (matched against
  `engines/resume/skills_taxonomy.py`, a lookup table), experience, projects, achievements, education, certifications.
- Layer 2 (optional, **off by default**): set `AI_EXTRACTION_ENABLED=true` and `ANTHROPIC_API_KEY`. This **sends the
  resume text to the Anthropic API**. The AI only fills fields the rules left empty, every value must appear in the
  source text, and any failure falls back to rule-based results.
- Achievements = bullets with a metric (%, numbers, currency) or an award word; they are separated from responsibilities.

### Candidate profile (`ai/schemas/candidate_profile.py`)
Sections: `contact`, `summary`, `technical_skills` (10 categories), `work_experience`, `projects`, `education`,
`certifications`, `achievements`; metadata `profile_id`, `resume_id`, `profile_version`, `parsing_status`,
`review_status`, `extraction_method`, `created_at/updated_at`; plus `missing_fields`, `review_flags`,
`validation_warnings`, `provenance`. Missing data stays `null`/empty. Values that are calculated (total experience,
current title taken from the latest role) are tagged `inferred` and flagged for review. `confidence_score` is never assigned.

### MongoDB collections
`resumes` (metadata, status, optional extracted text), `candidate_profiles` (one current profile per resume),
`profile_versions` (archived profiles: every correction/re-parse), `users` (single local user + active resume).
Indexes are created at API startup (`database/indexes.py`).

### API
| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/resumes/upload` | Upload + parse (201; 200 if duplicate) |
| GET | `/api/resumes` | List resumes |
| GET | `/api/resumes/{id}` | Details |
| POST | `/api/resumes/{id}/parse?force=` | Retry / re-parse (`force` required if a profile exists) |
| GET / PUT | `/api/resumes/{id}/profile` | Read / save corrections (new profile version) |
| GET | `/api/resumes/{id}/profile/versions` | Archived versions |
| GET | `/api/resumes/{id}/status` | Parsing status |
| POST | `/api/resumes/{id}/activate` | Make the active master resume |
| DELETE | `/api/resumes/{id}` | Delete file, resume, profile and versions |

### Privacy
Resume text is stored in MongoDB unless `STORE_RESUME_TEXT=false` (then fact-checking of edits is skipped).
Logs contain ids, types and sizes only – never resume content or personal details.
Nothing leaves your machine unless you enable AI extraction.

### Tests
`uv run pytest -v` – unit tests need no database; integration/safety tests create a throw-away database on your
configured MongoDB (random name, dropped afterwards) and skip if MongoDB is unreachable. Sample PDF/DOCX fixtures
(`tests/fixtures/`) contain **synthetic** data, so passing tests show the parser works on that layout – not that your
own resume will parse perfectly. Always review the extracted profile.

### Known limitations
Rule-based extraction is layout-sensitive (multi-column or heavily designed resumes may be mis-split); no OCR bundled;
`primary_domains` is never guessed; the AI layer is tested with mocks only.

## Phase 3 – AI matching engine

Scores the **verified** candidate profile against a job description and explains the result.
Open **Job Matching** in the sidebar, paste a JD, and press *Analyze & match*.
Methodology: [`docs/scoring_methodology.md`](docs/scoring_methodology.md). 30-JD evaluation (pending human review):
[`docs/phase3_evaluation_report.md`](docs/phase3_evaluation_report.md) - regenerate with
`.venv\Scripts\python.exe scripts/evaluate_matching.py`.

- **Works without Claude.** Parsing, eligibility, scoring and explanations are deterministic. Reports say whether the JD
  was parsed by rules (`jd_parser: deterministic`) or Claude + rules; a deterministic result is never presented as AI.
- **AI provider:** `AI_PROVIDER=anthropic` (Claude) or `gemini` (Google AI Studio key in `GEMINI_API_KEY`, model `GEMINI_MODEL`, default `gemini-3.1-flash-lite`;
  older 2.5 models are closed to new keys). The same consent flags and fact-checks apply to either; the text below says "Claude" but means the selected provider.
- **Claude (optional)** is used only if `ENABLE_AI_MATCHING=true` **and** `ANTHROPIC_API_KEY` is set **and**
  `AI_CONSENT_EXTERNAL_PROCESSING=true`. It refines skill classification (only skills found in the JD text are kept)
  and writes an optional summary (rejected if it contains numbers not in the facts, or calls an ineligible candidate
  eligible). It never sets a score. The JD text and non-identifying facts (skill names, scores) are sent - never your
  name, contact details, employers or project names. A Claude Code subscription does not include API credits; use a
  separate API key. Model/timeouts/retries: `CLAUDE_MODEL`, `CLAUDE_MAX_TOKENS`, `CLAUDE_TIMEOUT`, `CLAUDE_MAX_RETRIES`.
- **Config:** `config/scoring_weights.yaml` (weights, thresholds, credits, eligibility policy, `scoring_version`),
  `config/job_preferences.yaml` (your location/relocation/remote/salary/work-authorisation - unset = UNKNOWN),
  `config/skills.yaml` (skill synonyms and documented related-skill credit).
- **Profile review:** matching uses the corrected profile. If it is not marked *reviewed*, the API answers 409 and the
  page asks for explicit confirmation (`allow_unreviewed`).

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/matching/analyze-jd` | Parse a JD (cached per job/parser/skill dictionary) |
| POST | `/api/matching/score` | Match the active (or given) resume against one JD (`job` or `job_id`) |
| POST | `/api/matching/bulk-score` | Match against up to 25 JDs (per-item errors don't abort the batch) |
| GET | `/api/matching/results` | List matches (filters: score, eligibility, classification, title, company, dates) |
| GET | `/api/matching/{id}` / `/{id}/history` | Report / all matches of that resume against that job |
| POST | `/api/matching/{id}/rerun` | Re-score with the current profile + configuration (adds history) |
| GET | `/api/matching/thresholds` | Weights, thresholds, version, AI status |

Collections: `jobs` (JD text once per distinct job, cached analysis), `job_matches` (reports; deleted with their resume).

## Phase 4 – Job discovery

Finds, normalises, de-duplicates and stores jobs from **permitted** sources. Open **Job Discovery** in the sidebar.
The registry `config/platform_policies.yaml` decides what may run - **fail closed**: unknown sources and invalid statuses are
`DISABLED`; a status can never unlock code that does not exist (there is no scraping code for LinkedIn/Indeed/Naukri at all).

| Source | Status | What you can do |
|---|---|---|
| LinkedIn | `MANUAL_ONLY` | Import a job you found yourself (paste details; the URL is stored, never fetched) |
| Indeed | `MANUAL_ONLY` | same |
| Naukri | `PENDING_POLICY_REVIEW` | automation off until a human verifies the policy; manual import allowed |
| Company career pages | `ENABLED` for **companies you list** in `config/career_sources.yaml` | Greenhouse + Lever public read APIs; generic pages only if you set `approved: true`, they expose JSON-LD `JobPosting` data and robots.txt allows (fails closed if unreadable); Workday: detected, `PENDING_POLICY_REVIEW`, not implemented |

*Policy review basis:* desk research on 2026-11-12 over each platform's published terms/documentation and secondary summaries
(not the full legal texts, not legal advice). `needs_human_confirmation: true` marks every entry that still needs your review.

- **Guardrails (cannot be turned off by an adapter):** per-source host allow-list, https only, no private/local addresses
  (SSRF), rate limit + per-run request budget, honest `User-Agent`, no cookies/auth/logins. HTTP 401/403/429 or a CAPTCHA/login
  wall stops that source for the run - **never retried, never worked around**.
- **Pipeline:** adapter -> `RawJob` -> `engines/discovery/job_normalizer.py` (titles, companies, locations, work mode, employment
  type, dates, salary, skills/experience via the Phase 3 JD parser, canonical URL; original values kept on each source reference;
  unknown stays `null`) -> `deduplication.py` -> MongoDB `jobs`.
- **Duplicates:** exact first (same source + id + company, or same canonical URL with tracking parameters removed); then same company
  + same title + compatible location + description similarity >= 0.85 = **merge** (all source references kept). Anything
  uncertain (similarity 0.5-0.85, similar-but-different title, nothing to compare) is **flagged for review, not merged**. Same
  company + other city / other requisition id / unrelated description = a different vacancy. A changed vacancy is updated in place
  (`discovered_at` preserved, `last_updated_at` bumped).
- **Collections/indexes:** `jobs` (shared with Phase 3; unique `job_id`, `content_hash`; indexes on company key, canonical URL,
  source references, posted date, status, review flag) and `discovery_runs`.
- **Phase 3 link:** a discovered job can be scored with `POST /api/matching/score {"job_id": ...}`.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/jobs/sources` | Source availability, status, reason |
| POST | `/api/jobs/discover` | Start discovery (202; background) - non-permitted sources are skipped with the reason |
| GET | `/api/jobs/discovery/status` | Progress/stats of the latest (or a given) run |
| POST | `/api/jobs/import` | Import by URL and/or details (manual sources never fetched; Greenhouse/Lever URLs use their API) |
| GET | `/api/jobs` | List/filter/sort (source, title, company, location, work mode, skill, experience, dates, review flag) |
| GET | `/api/jobs/{id}` | Details incl. original source values |
| POST | `/api/jobs/{id}/refresh` | Re-read from a permitted source (404 -> `closed`; manual jobs: 409) |

Preferences: `config/job_preferences.yaml` (titles, skills, locations, companies, work-mode yes/no flags, employment types,
experience range, posted-within-days, minimum salary). A work mode is excluded only if you said *no*; unknown job values never
exclude a job.

## Phase 5 – Document generation

Creates a job-specific **resume**, **cover letter** and **screening answers** from your *verified* profile, fact-checks them, lets you
edit, requires your explicit approval, and exports DOCX + PDF. Open **Document Generation** in the sidebar.

- **Source of truth:** the candidate profile you reviewed in Resume Manager (an unreviewed profile needs explicit confirmation, as in
  Phase 3) plus the job (Phase 3 pasted or Phase 4 discovered) and its match report. Nothing else is used.
- **Resume customisation (deterministic, always available):** every role stays with its title, company and dates; skills are *reordered*
  by relevance (nothing added); bullets are your own words, **verbatim**, selected and ordered by relevance to the job; projects ranked and
  capped; a summary written only from verified facts (years are floored - `7.4` becomes "7+ years" - and are omitted if unknown). Every
  element records the profile path it came from.
- **Cover letter / screening answers:** assembled from the same fact pack. Missing details are never guessed: notice period, salary
  expectation, relocation and work authorisation come only from `config/job_preferences.yaml` or what you type in; otherwise the answer is a
  `needs_input` placeholder that blocks approval. Motivation/"challenging project" answers are drafts flagged for your review.
- **Claude (optional):** may only re-word the summary, letter and answers from the fact pack, and only if `ENABLE_AI_MATCHING=true`, an API
  key is set **and** `AI_DOCUMENTS_CONSENT=true`. Your name, email, phone, links and location are never sent. Every AI text goes through the
  same fact check; if it contains an unsupported claim it is discarded and the deterministic text is used (noted on the document).
- **Fact check** (`ai/validators/document_validator.py`, re-exported from `factuality_validator.py`): employers, titles, dates, education,
  certifications and contact details must equal the profile; skills must be in the profile; numbers/metrics, experience durations,
  leadership claims, degrees, certifications, organisations and personal details in free text are checked against profile evidence.
  *Errors* block approval and export; *warnings* must be acknowledged; edited wording is flagged.
- **Versions:** generating, regenerating or editing always creates a **new version** (old ones are kept); a document is approved only by an
  explicit action, only if validation of the exact current content has no errors; approving a version supersedes the previously approved one.
  Exports of unapproved documents carry a visible `DRAFT` footer and file name. The master resume files are never written to.
- **Export:** DOCX (python-docx: clickable links, section rules, right-aligned dates) and PDF (reportlab). PDFs use Arial/DejaVu when found;
  otherwise Helvetica, and any character a PDF font cannot draw is replaced by `?` **and reported** (DOCX always keeps full Unicode).
  Files go to `storage/generated/` under unique names and are served only through the API after an integrity (SHA-256) check.

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/documents/resume/generate` | Customised resume (also `cover-letter/generate`, `screening/generate`) |
| GET | `/api/documents` | List (latest version per document by default) |
| GET | `/api/documents/{id}` | Content, validation, export list, `profile_stale` |
| PUT | `/api/documents/{id}` | Save edited content as a **new version** (cannot change status or metadata) |
| POST | `/api/documents/{id}/validate` | Re-run the fact check against the current profile |
| POST | `/api/documents/{id}/approve` | Explicit approval (`acknowledge_warnings` if there are warnings) |
| POST | `/api/documents/{id}/export` | Create DOCX and/or PDF |
| GET | `/api/documents/{id}/download/{docx|pdf}` | Download the latest export |
| GET | `/api/documents/{id}/versions` / `/compare/{other}` | Version history / text diff |

Collection `documents` (one document per version): ids (`document_id`, `group_id`, `profile_id`, `job_id`, `match_id`), `version`,
`parent_version_id`, `generated_at`, `template_version`, `generator`/`ai_model`, `content`, `validation` (status, issues, content hash),
`review_status` (`draft`/`approved`/`superseded`), `approved_at`, `exports[]` (format, stored file id, SHA-256, size).

## Phase 6 – Application execution

Prepares an application from your verified profile and **approved** documents, lets you review it, and - only where automation is permitted and
you explicitly approve - submits it with a real browser (Playwright). Everywhere else you get a **manual handoff** package. Pages:
**Application Queue** and **Applied Jobs**.

- **Closed by default.** `APPLICATION_AUTOMATION_ENABLED=false`. Even when on, a site is automated only if `config/application_policies.yaml`
  allows it: LinkedIn, Indeed, Naukri, Workday and unknown sites are manual handoff only; Greenhouse, Lever and generic career pages need *you* to list the
  site under `authorized_sites` after checking the employer's terms (the list ships empty). Greenhouse boards use reCAPTCHA, so they hand off anyway.
  Policy research (2026-11-12, desk research, not legal advice) is recorded in that file.
- **Never bypassed:** CAPTCHAs, bot checks, login walls and HTTP 401/403/429 end the run and switch to manual handoff. No logins are performed, no passwords
  are stored, no proxies/user-agent changes/stealth. Saved sessions (optional, `PERSIST_BROWSER_SESSIONS=true` + `SECRET_KEY`) are encrypted and expire.
- **Flow:** prepare (open page -> detect form -> map fields -> fill -> upload approved files -> validate) -> **you approve exactly that plan** -> submit.
  Approval is bound by hash to the values, files and form; if the profile, documents, form or policy changed, submit is refused and approval withdrawn.
- **Field mapping** uses labels, names, placeholders and DOM relations, not CSS selectors. Values come only from the profile, approved screening answers
  or your own typed answers. Consent, demographics, visa/work-authorisation, salary and unknown required questions are never auto-answered. Unidentified
  fields, multi-step and dynamic forms stop automation.
- **States:** DISCOVERED, READY_FOR_REVIEW, AWAITING_USER_INPUT, APPROVED, SUBMITTING, SUBMITTED, SUBMISSION_FAILED, VERIFICATION_PENDING, VERIFIED,
  MANUAL_HANDOFF_REQUIRED. SUBMITTED needs a confirmation message *and* the form gone; VERIFIED needs a reference number (or your own recorded
  confirmation). Anything uncertain is VERIFICATION_PENDING and is **never retried**. One application per resume+job (unique idempotency key).
- **Setup:** `uv sync`; Playwright uses its Chromium (`uv run playwright install chromium`) or an installed Edge/Chrome automatically (`BROWSER_CHANNEL`).

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/applications/prepare` | Prepare (detect, map, fill, validate) |
| GET | `/api/applications`, `/{id}`, `/{id}/status`, `/{id}/events` | List / details / evidence / immutable history |
| GET | `/api/applications/policy/{job_id}` | May this job be automated? |
| POST | `/{id}/validate`, PUT `/{id}/answers` | Re-check; give your own answers |
| POST | `/{id}/approve`, `/{id}/submit` | Explicit approval; permitted submission |
| POST | `/{id}/manual-handoff`, `/manual-handoff/opened`, `/{id}/confirm` | Manual route and recording your own outcome |

Tests drive a real browser only against **local** forms (`tests/fixtures/form_server.py`); no real application is ever submitted.

## Phase 7 – Scheduler, queue and notifications

Runs discovery on a schedule, queues good matches, **prepares** their applications in the background, enforces daily limits, reports, and (optionally)
e-mails you. It never approves or submits anything: prepared applications wait for you on the **Application Queue** page.

- **Scheduler** (`scheduler/scheduler.py`, no extra dependency): daily discovery at 07:00 and report at 20:00 in `Asia/Kolkata` (`SCHEDULER_TIMEZONE`,
  times in `config/automation_limits.yaml`), worker every minute. Persistent state in MongoDB: a restart resumes it, makes up a missed run once if it was due
  within the grace period, marks crash-orphaned runs `interrupted`, and an atomic database lock prevents overlapping runs. It never starts on its own unless you
  press Start (persisted) or set `SCHEDULER_AUTOSTART=true`.
- **Discovery** only through sources your Phase 4 policy permits; results are normalised, de-duplicated, matched (Phase 3) and queued if score >= `min_match_score`
  and eligibility is not FAIL. Priority = score (+10 if eligibility PASS).
- **Queue** (`application_queue`): one entry per resume+job (unique key), states queued / processing / awaiting_approval / completed / failed / manual_handoff /
  needs_verification / paused / cancelled; atomic claims, leases, recovery after a crash, pause/resume/cancel (never for a possibly-submitted application).
- **Worker** calls only Phase 6 `prepare`. Permanent problems fail at once; temporary ones retry with doubling back-off up to `max_retries_per_application`; before any
  retry it checks whether the application may already have been sent - if so the task becomes `needs_verification` and is never retried.
- **Limits** (`config/automation_limits.yaml`) are hard caps; the dashboard can only lower them (and raise the minimum score). The daily target (20-30) is a goal, not a quota.
- **E-mail** (`notifications/`): Gmail API over OAuth 2.0 using your own client; credentials are stored Fernet-encrypted (`SECRET_KEY`) via `scripts/gmail_setup.py`.
  Not configured or failing = recorded as skipped/failed, nothing else is affected. Messages contain counts and job titles only.
- **Reports** (`daily_reports`): discovered, by source, duplicates, matched, Excellent/Strong/Good, eligible, awaiting approval, submitted (page-confirmed only; self-reported
  separately), verified, failed, manual handoffs, pending verification, limit usage. Prepared or attempted applications are never counted as submitted.
- **API:** `/api/scheduler/{status,start,stop,pause,resume,discover,worker/run,runs,limits}`, `/api/queue[/{id}[/pause|resume|cancel]]`, `/api/notifications[/test]`,
  `/api/reports/daily[/generate]`. Every state-changing call needs `X-Admin-Token` = `ADMIN_TOKEN` (empty = refused). Pages: **Dashboard** (status) and **Automation** (controls).

## Phase 8 - Dashboard (React management interface)

One interface for everything. Start it:

```powershell
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000      # backend (also starts the scheduler if you enabled it)
cd web; npm start                                                  # web dashboard: http://127.0.0.1:8502  (build once with: npm install; npm run build)
```

The dashboard is a React single-page app in `web/`. A small Node server (`web/server/server.mjs`) serves it and forwards `/api` to the backend. **`ADMIN_TOKEN` stays on that server:**
the browser only marks an operational call with `X-Admin-Action: 1`, and the server then adds the real `X-Admin-Token`. The browser therefore never sees the token (verified by the browser tests), needs no CORS,
and a token sent by a browser is discarded.

Pages (sidebar): **Dashboard** (metrics, activity charts, pending actions, upcoming interviews, recent activity, system status) - **Job Discovery** (filters incl. match score and discovery date, pagination, duplicate status, history, manual discovery) -
**Resume Manager** (upload, edit profile, versions, verification status, generated-resume preview) - **Job Matching** (paste/upload a JD, score, breakdown, history with recommendation filter) -
**Application Queue** (overview of everything awaiting action; approve / reject / pause / cancel / manual handoff) - **Applied Jobs** (search, evidence, notes, record outcomes) -
**Interview Tracker** - **Analytics** (date range, rates, source performance, CSV export) - **Settings** (preferences, matching weights, caps, scheduler/retries, browser, e-mail, AI status, platform permissions) -
plus **Document Generation** and **Automation** from earlier phases.

- Every number comes from stored records through the API (`/api/dashboard/summary`, `/api/analytics/*`); empty data shows an empty state, never sample numbers. The UI holds no business logic and never touches MongoDB.
- Definitions (same everywhere): *matching* = latest match score >= the minimum and eligibility not FAIL; *submitted* = state SUBMITTED/VERIFIED (evidence required); success/failure/unknown rates use attempts
  (submitted + pending verification + failed after sending) as the denominator, so they add up to 100 %. Days are local days (Asia/Kolkata by default).
- **Settings are validated before saving**; secrets (API keys, tokens, OAuth, SECRET_KEY) are never shown or editable - only "configured / not configured". Saving needs `ADMIN_TOKEN` (set it for the API and for the web server). YAML comments are preserved; a scoring change bumps `scoring_version`, backs up the old file under `config/backups/` and shows before/after. `.env` edits are limited to an allow-list
  and need an API restart. Platform permissions are read-only here (edit the policy files deliberately).
- Important actions (reject, delete interview) ask for confirmation. New endpoints: `/api/interviews`, `/api/dashboard/summary`, `/api/analytics/{overview,export}`, `/api/settings[/{preferences,scoring,automation,env}]`,
  `/api/applications/{page,{id}/notes,{id}/reject}`, `/api/jobs/{page,discovery/runs}`.

## Troubleshooting

- **Automation buttons are disabled** - set `ADMIN_TOKEN` in `.env` and restart the API and the web server (`npm start`).
- **Everything is "manual handoff"** - expected by default; see the policy file and `APPLICATION_AUTOMATION_ENABLED`.

- **A document cannot be approved** - open "Must fix before approval": it lists the exact unsupported claim. Edit the text, or correct the
  profile in Resume Manager if the profile itself is wrong.
- **Discovery finds nothing** - company career pages only read companies listed in `config/career_sources.yaml` (empty by default).
- **A qualified candidate is "Not eligible" because of one skill** - the mandatory check needs the skill spelled in the
  profile (related skills only earn partial credit). Review the profile in Resume Manager and add skills you genuinely
  have, or adjust `eligibility.mandatory_skills_fail_if_missing_fraction_above` in `config/scoring_weights.yaml`.
- **`/health/database` returns 503** – MongoDB isn't running or `MONGODB_URI` is wrong.
- **The dashboard says backend unreachable** – start uvicorn, and check `BACKEND_BASE_URL` for the web server.
- **`node` / `npm` not recognised** – install Node.js (`winget install OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements`) and open a new terminal.
- **`uv sync` hangs on a cache lock** – another `uv` process is running; wait for or stop it.
- **Wrong Python** – `.python-version` pins 3.11; run `uv python install 3.11`.
- **`git` not recognised** – install Git (see Prerequisites) and reopen the terminal.


steps 

uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000
npm start