# AI JobPilot - Application Overview

A complete description of what AI JobPilot is, how it works, how to run it, what each part does, and what is and is not
proven. Written 2026-11-23 against the current code. For day-to-day steps see `USER_GUIDE.md`; for how scores are
calculated in detail see `scoring_methodology.md`; for setup on a server see `DEPLOYMENT.md`.

> `PROJECT_STATUS_REPORT.md` (2026-11-13) and `TESTING_REPORT.md` describe an earlier state. Where they differ, this
> document is newer.

---

## Contents

1. [What the application does](#1-what-the-application-does)
2. [The rules it always follows](#2-the-rules-it-always-follows)
3. [How it works end to end](#3-how-it-works-end-to-end)
4. [Architecture](#4-architecture)
5. [Job sources](#5-job-sources)
6. [Resume intelligence](#6-resume-intelligence)
7. [Job matching: score and eligibility](#7-job-matching-score-and-eligibility)
8. [Documents](#8-documents)
9. [Applications](#9-applications)
10. [Scheduler and daily automation](#10-scheduler-and-daily-automation)
11. [The web dashboard, page by page](#11-the-web-dashboard-page-by-page)
12. [Configuration files](#12-configuration-files)
13. [Data stored in MongoDB](#13-data-stored-in-mongodb)
14. [API reference](#14-api-reference)
15. [Running the application](#15-running-the-application)
16. [Testing](#16-testing)
17. [Current status: what is proven and what is not](#17-current-status-what-is-proven-and-what-is-not)
18. [Known limitations](#18-known-limitations)
19. [Troubleshooting](#19-troubleshooting)
20. [Glossary](#20-glossary)

---

## 1. What the application does

AI JobPilot is a personal job-search assistant that runs on your own computer. It:

1. **Reads your resume** and turns it into a structured, reviewed profile (titles, dates, skills, projects, education).
2. **Finds jobs** automatically from the public job boards of the companies you list, and lets you bring in jobs from
   LinkedIn, Naukri, Indeed, Instahyre or any other site by pasting them.
3. **Scores every job against your profile** (0-100) and separately checks whether you meet the job's hard
   requirements (PASS / FAIL / UNKNOWN), with the reasons for both.
4. **Prepares applications** for good matches: a tailored resume, a cover letter and screening answers, built only from
   facts in your verified profile.
5. **Tracks everything**: which jobs you applied to, their status, interviews, and analytics.

It is an **assisted** system. It does the searching, reading, scoring and paperwork; **you** decide and apply. It never
submits an application without your explicit approval, and on restricted sites it never submits at all.

---

## 2. The rules it always follows

These are enforced in code and covered by safety tests (`tests/safety/`).

| Rule | What it means in practice |
|---|---|
| **No scraping of restricted sites** | LinkedIn, Naukri, Indeed and Instahyre are never fetched, searched or logged in to. You open them in your own browser and paste jobs in. |
| **No automated login, no CAPTCHA solving, no evasion** | No passwords are stored; no anti-bot workarounds, proxies or header tricks exist in the code. |
| **Only permitted sources are automatic** | A source must have a policy entry (`config/platform_policies.yaml`) and an authorised method (a documented public API). Unknown = disabled. |
| **Nothing is submitted without your approval** | An approval is recorded by the backend and bound to the exact prepared application. The background worker can only *prepare*; it has no code path to approve or submit. |
| **No invented candidate data** | Missing facts stay empty and are listed as missing. Notice period, salary, relocation and work authorisation are never guessed. |
| **Score and eligibility are separate** | A high score never turns a FAIL into a PASS. UNKNOWN is never shown as PASS. |
| **Evidence-based statuses** | "Submitted" and "Verified" need evidence. A job you mark as applied is labelled "recorded by you", never "verified by the site". |
| **Personal data stays local** | Resume content and personal data are never logged. The optional AI service never receives your resume's personal details. |

---

## 3. How it works end to end

```
 Your resume ──► Resume Manager ──► reviewed profile
                                         │
 Company job boards (automatic, daily) ─┐│
 LinkedIn / Naukri / other (you paste) ─┼┴─► Jobs (deduplicated, MongoDB)
                                        │         │
                                        │         ▼
                                        │   Job Matching: score 0-100 + eligibility PASS/FAIL/UNKNOWN + reasons
                                        │         │
                                        │         ▼  (PASS and score ≥ 70)
                                        │   Application Queue: documents prepared (resume, cover letter, answers)
                                        │         │
                                        │         ▼
                                        └── You apply (manual handoff) ──► flip "Applied" ──► Applied Jobs / Analytics
```

1. **Profile** - upload the resume, correct the extracted profile, mark it reviewed.
2. **Preferences** - job titles, locations, work mode, age of postings (Settings).
3. **Discovery** - every morning at 07:00 (or on demand) the app reads the configured company boards, keeps jobs that
   fit your preferences, and stores new ones. Pasted jobs from other sites go through the same duplicate check.
4. **Matching** - every new job is scored and checked for eligibility straight away.
5. **Queue** - jobs that are PASS with a score of at least `min_match_score` (70) are queued; between 09:00 and 21:00
   the worker prepares their documents.
6. **You apply** - for every current source this is a *manual handoff*: you open the job link and apply with the
   prepared documents, then flip the **Applied** switch.
7. **Track** - Applied Jobs, Interview Tracker, Dashboard and Analytics show progress.

---

## 4. Architecture

| Layer | Technology | Where |
|---|---|---|
| Web dashboard | React + Vite + TypeScript, served by a small Node server that forwards `/api` | `web/` (`web/src/pages/*.tsx`, `web/server/server.mjs`) |
| API | Python 3.11, FastAPI | `backend/main.py`, `backend/api/routes/`, `backend/services/` |
| Engines (business logic) | Python | `engines/resume`, `engines/discovery`, `engines/matching`, `engines/generation`, `engines/application` |
| Source integrations | Python adapters, policy registry | `integrations/` (career pages: Greenhouse, Lever; manual-only adapters for LinkedIn, Naukri, Indeed, Instahyre) |
| Scheduler | In-process scheduler with locks, queue and worker | `scheduler/` |
| Database | MongoDB via PyMongo, repositories only | `database/connection.py`, `database/repositories/` |
| AI (optional) | Anthropic Claude or Google Gemini, opt-in, deterministic fallback | `ai/claude_client.py`, `ai/prompts/` |
| Configuration | YAML + `.env` via Pydantic Settings | `config/` |

**Design points**

- The browser talks only to the API (never to MongoDB). All browser requests go through `web/src/api.ts`.
- The admin token (`ADMIN_TOKEN`) never reaches the browser; the Node server adds it to calls the page marks as admin
  actions.
- Routes are thin; the logic lives in services and engines, so it is testable without the web layer.
- The deterministic matching engine is the source of truth. AI may only reword text and re-classify skills that appear
  in a job description; it never changes a score.

---

## 5. Job sources

### 5.1 Automatic: company career pages

Read through documented public job-board APIs (Greenhouse Job Board API, Lever Postings API). One listing request per
company per run. Only companies listed in `config/career_sources.yaml` are read.

**41 companies are configured** (each checked to list AI / ML / data roles):

| Group | Companies |
|---|---|
| Global, large India teams | Databricks, GitLab, Twilio, Coinbase, Pinterest, Reddit, Samsara, Airbnb, Anthropic, MongoDB, Elastic, Stripe, Rubrik, Zscaler, Glean, Okta, Celonis, Starburst, Netskope, NICE, Point72, Five9, Toast, Outreach, KKR |
| India-based or India-heavy | InMobi, Glance, Sigmoid, Turing, 6sense, Zeta Global, Paytm, PubMatic, DeepIntent, Mindtickle, Meesho, Zenoti, HackerRank, Netradyne, FamPay, Zeta |

To add a company: add a line `- {name: Company, ats: greenhouse|lever, board_token: <token>}` and restart the API
(the scheduler loads the list at start-up).

**Filters applied to every discovered job** (from Settings → Preferences):

| Filter | Your current value |
|---|---|
| Job titles (title must contain one) | machine learning engineer, ml engineer, ai engineer, generative ai, genai, llm, data scientist, applied scientist, data analyst, business analyst, computer vision, nlp, machine learning, data science, ai/ml, applied ai, ai product engineer, ai researcher |
| Locations | Pune, India ("India" matches e.g. "Bengaluru, India") |
| Remote jobs must be in those locations | yes (drops "Remote - United States") |
| Work modes | remote, hybrid, onsite |
| Posting age | 90 days or newer (undated jobs are kept) |

**Caps.** At most 300 *new jobs that pass your filters* per run, and `max_jobs_discovered_per_day` (300) per day.
Every configured company is always read; the cap only limits how many matching jobs are processed.

### 5.2 Manual: LinkedIn, Naukri, Indeed, Instahyre, any site

These sites forbid automated collection, so the app never contacts them. On **Job Discovery**:

| Tool | What it does |
|---|---|
| **Search on LinkedIn / Naukri** | Builds a search link (LinkedIn keywords and location are pre-filled from your profile) that opens in your own browser. |
| **Quick import (paste)** | Paste one job's link and text; title is suggested, company is never guessed. |
| **Naukri bulk paste** | Copy a whole Naukri results page yourself (Ctrl+A, Ctrl+C), paste it; the app splits it into jobs, you tick which to import. |
| **Import a job manually** | A form for a job from any other website. |

Every imported job is checked for duplicates, stored with its source (LinkedIn, Naukri, ...), and **scored
immediately**. Pasted links are stored without tracking parameters and are never opened by the app.

### 5.3 Duplicates

A job is matched to an existing one by source job id, canonical URL, or company + title similarity. Clear duplicates
are merged (both sources kept); uncertain ones are flagged "possible duplicate" for you to review, never merged
automatically.

---

## 6. Resume intelligence

- **Upload** (PDF or DOCX): content is validated (not just the extension), stored under a server-generated name in
  `storage/resumes/`, never overwritten.
- **Extraction**: rule-based, optionally helped by AI (opt-in; AI may only fill empty fields with text that appears in
  the resume).
- **Profile**: contact, summary, work experience, projects, education, certifications, skills by category.
- **Review**: you correct and confirm it. Every edit creates a new profile version (old versions are archived).
  Matching uses only a *reviewed* profile unless you explicitly allow otherwise.
- **Flags**: values that were calculated (for example total experience from dates) are flagged for review; values not
  found in the resume text (for example links you typed in) are shown as validation warnings. These are reminders,
  not errors.

---

## 7. Job matching: score and eligibility

Full method and before/after evidence: `scoring_methodology.md`. Scoring version **1.1**, job-description parser
version **1.2**.

### 7.1 Job description analysis

The parser splits a job description into sections (requirements, preferred, responsibilities, company text, legal
text) and classifies each skill as **mandatory**, **preferred**, **example** or **mentioned**. It reads minimum
experience (taking the lowest of alternative routes), education and certifications. Company boilerplate, pay ranges,
the employer's own products, choose-N lists and company history ("more than 30 years of investing experience") are not
treated as requirements.

### 7.2 Match score (0-100)

| Category | Weight |
|---|---:|
| Technical skills | 30 |
| Relevant experience | 20 |
| Projects | 15 |
| Responsibilities | 15 |
| Seniority | 10 |
| Education | 5 |
| Location & work mode | 5 |

A **role-fit factor** is applied last: same job function ×1.0, adjacent ×0.8, different (sales, marketing, finance,
legal, HR) ×0.4. It is stored and shown in the report.

Classification: Excellent ≥ 90, Strong ≥ 80, Good ≥ 75, Moderate ≥ 60, otherwise Low. "Recommended" needs score ≥ 75
**and** eligibility PASS.

### 7.3 Eligibility (PASS / FAIL / UNKNOWN)

Checks only hard requirements: mandatory skills (an exact skill or a documented near-equivalent with credit ≥ 0.7),
minimum experience, mandatory education/certifications, and preferences you set as hard. Preferred skills never cause a
FAIL. If something cannot be confirmed the result is UNKNOWN, never PASS.

### 7.4 Explanation

Every report lists matched skills, missing mandatory and preferred skills, partial matches, experience and education
notes, project relevance, strengths, weaknesses, rejection reasons and limitations of the analysis.

---

## 8. Documents

For a job, the app generates (Document Generation page):

- **Tailored resume** - your verified bullets, verbatim; skills reordered for the job, never added.
- **Cover letter** - built from a fact pack of your verified profile.
- **Screening answers** - personal details you did not provide stay as `needs_input`.

Every document is validated against your profile; anything that fails validation cannot be approved. Every edit is a
new version; drafts are exported marked DRAFT; your master resume is never changed. Export: DOCX and PDF.

---

## 9. Applications

### 9.1 Life cycle

`DISCOVERED → READY_FOR_REVIEW / AWAITING_USER_INPUT → APPROVED → SUBMITTING → SUBMITTED → VERIFIED`, or
`MANUAL_HANDOFF_REQUIRED → SUBMITTED (recorded by you)`. A failed attempt is `SUBMISSION_FAILED`; an uncertain outcome is
`VERIFICATION_PENDING` and is never retried automatically.

### 9.2 Where automation is allowed

`config/application_policies.yaml`:

| Platform | Status | Why |
|---|---|---|
| LinkedIn, Indeed, Naukri, Instahyre | MANUAL_HANDOFF | Terms forbid automated applications |
| Greenhouse boards | AUTHORIZATION_REQUIRED | Public forms use reCAPTCHA; no permission found |
| Lever boards | AUTHORIZATION_REQUIRED | Submit API needs an employer-issued key |
| Workday | MANUAL_HANDOFF | Needs a candidate login |

**Result today: every application is a manual handoff.** The app prepares the package (link, approved documents,
answers, contact details, what is missing); you apply on the site.

### 9.3 The "Applied" switch

On **Job Matching → G. Match results** and **Job Discovery → Discovered jobs**, every job has an **Applied** switch.
After you apply yourself, flip it and confirm. The app records the application as *submitted, recorded by you* (via the
normal steps, with an audit event), the switch locks, and the job appears on Applied Jobs. Nothing is sent anywhere.
Add a confirmation reference later on Applied Jobs to mark it verified.

---

## 10. Scheduler and daily automation

Started from the **Automation** page (needs `ADMIN_TOKEN`) or with `SCHEDULER_AUTOSTART=true`. Runs inside the API
process, time zone `Asia/Kolkata`.

| Job | When | What |
|---|---|---|
| Daily discovery | 07:00 (window 06:00-22:00) | Reads the company boards, stores new matching jobs, scores them, queues PASS + score ≥ 70 |
| Application worker | every minute, only 09:00-21:00 | Prepares queued applications (documents, form check). Never approves or submits. |
| Daily report | 20:00 | Summary of the day (and e-mail if configured) |
| Notification retry | every few minutes | Re-sends failed notifications |

**Limits** (`config/automation_limits.yaml`, hard caps; the dashboard can only lower them): 300 jobs discovered/day,
60 applications prepared/day, 20 submissions/day, 3 retries, minimum match score 70.

Idle worker runs ("nothing to do") are hidden from the Dashboard's Recent activity.

---

## 11. The web dashboard, page by page

Open http://127.0.0.1:8502 (or the port in `WEB_PORT`, e.g. 8501).

| Page | What you see and do |
|---|---|
| **Home** | Backend and database status, "Search once, now", key numbers (jobs found, fit your preferences, good matches, awaiting approval, submitted, interviews), how-it-works steps, links to every page. |
| **Dashboard** | Job, application and queue counts, a daily activity chart, pending actions, upcoming interviews, recent activity, system status, today's limits. "Good matches" uses the same rule as Job Matching. |
| **Job Discovery** | 1) Sources and their policy status; 2) run a discovery with your preferences or one-off filters, and see the last run's numbers; LinkedIn and Naukri search links, quick import, Naukri bulk paste, "Your LinkedIn jobs" table, manual import; 3) the discovered jobs list with filters ("Only jobs that fit my preferences" on by default), Applied switches and job details. |
| **Resume Manager** | Upload, parse, review status, fields needing review, validation warnings, edit and confirm the profile, versions. |
| **Job Matching** | A) your profile; B) paste a job description to score it; C-F) the full report for the selected job; G) match results: number cards (stored, fit preferences, eligible, good matches, applied, still to apply), filters (score range, eligibility, experience, title, company, recommendation, **application: applied / not applied yet**, dates, fit preferences), a table with Applied switch, score bar, eligibility, experience, role fit, source and link, and **Details** (opens the report below the table). |
| **Document Generation** | Generate, edit, validate, approve and export documents per job. |
| **Application Queue** | Overview tabs - **Needs your action** (default; everything waiting for you, including manual handoff), awaiting approval, queued & processing, manual handoff, failed / needs verification - each with an **Open** button. Then per job: policy, documents, review, approve/reject, or the manual-handoff package. The job picker lists jobs that fit your preferences, best score first ("Show all stored jobs" to see everything). |
| **Applied Jobs** | Your applications with search, notes, outcomes and confirmation references. |
| **Interview Tracker** | Add and update interviews; reminders. |
| **Analytics** | Rates (applied, confirmed, pending, failed), source performance over a date range, CSV export. |
| **Automation** | Start / stop / pause the scheduler, run discovery or the worker now, run history, day-to-day limits. |
| **Settings** | Preferences (titles, locations, work mode, remote-within-locations, experience, salary, notice period, posting age, keywords), scoring weights and thresholds, automation caps, scheduler, browser, notifications, AI, sources and platforms, security. Writes are validated before a file is replaced and keep the file's comments; scoring changes bump the scoring version. |

The layout uses the full browser width and adapts to window size and zoom; wide tables scroll inside their own box.

---

## 12. Configuration files

| File | Contents |
|---|---|
| `.env` | Environment: MongoDB URI, ports (`API_PORT`, `WEB_PORT`), `ADMIN_TOKEN`, `SECRET_KEY`, AI keys and flags, scheduler autostart. Never commit it. |
| `config/job_preferences.yaml` | Your preferences (also editable on Settings). |
| `config/career_sources.yaml` | Companies whose public job boards are read. |
| `config/scoring_weights.yaml` | Weights, thresholds, eligibility rules, role-fit families, `scoring_version`. |
| `config/skills.yaml` | Skill dictionary, aliases and related-skill credits. |
| `config/automation_limits.yaml` | Daily caps, windows, scheduler times. |
| `config/platform_policies.yaml` | Discovery permission per source (ENABLED / MANUAL_ONLY / DISABLED ...). |
| `config/application_policies.yaml` | Application automation permission per platform. |

---

## 13. Data stored in MongoDB

Database `ai_jobpilot` (from `MONGODB_DATABASE`).

| Collection | Holds |
|---|---|
| `users` | The default user and the active resume |
| `resumes`, `candidate_profiles`, `profile_versions` | Uploaded resumes, the current profile, archived versions |
| `jobs` | Every stored job with its sources, parsed requirements and duplicate information |
| `job_matches` | Match reports (append-only; the newest per job is shown) |
| `discovery_runs` | Each discovery run's numbers and notes |
| `documents` | Generated documents and their versions |
| `applications`, `application_events` | Applications and their append-only event history |
| `application_queue` | Queue tasks for the worker |
| `scheduler_state`, `scheduler_runs`, `scheduler_locks`, `automation_counters` | Scheduler state, run history, locks, daily counters |
| `notifications`, `daily_reports` | Notification log and daily reports |
| `interviews` | Interviews you added |

---

## 14. API reference

Interactive documentation: http://127.0.0.1:8000/docs. Main groups (all under `/api` except health):

| Prefix | Purpose | Key endpoints |
|---|---|---|
| `/health` | Status | `GET /health`, `GET /health/database` |
| `/api/resumes` | Resumes and profiles | upload, list, parse, get/put profile, versions, activate, delete |
| `/api/jobs` | Discovery and jobs | `GET /sources`, `POST /discover`, `GET /discovery/status`, `GET /discovery/runs`, `POST /import` (scores the job straight away), `GET /page` (filters incl. `preferred_only`), `GET /{id}`, `POST /{id}/refresh`, `GET /linkedin/suggestions`, `GET /linkedin/table` |
| `/api/matching` | Scoring | `POST /analyze-jd`, `POST /score`, `POST /bulk-score`, `GET /results` (filters incl. `preferred_only`, `applied`), `GET /results/count`, `GET /counts`, `GET /thresholds`, `GET /{match_id}`, `GET /{match_id}/history`, `POST /{match_id}/rerun` |
| `/api/documents` | Documents | generate resume / cover letter / screening, list, get, versions, compare, edit, validate, approve, export, download |
| `/api/applications` | Applications | `POST /prepare`, `POST /record-applied`, `POST /states`, list, page, policy, get, validate, answers, approve, submit, manual-handoff, confirm, notes, reject, status, events, screenshots |
| `/api/queue` | Queue | list, get, enqueue, pause, resume, cancel |
| `/api/scheduler` | Scheduler | status, start, stop, pause, resume, discover now, run worker now, runs, limits |
| `/api/dashboard`, `/api/analytics` | Metrics | `GET /api/dashboard/summary`, `GET /api/analytics/overview`, `GET /api/analytics/export` |
| `/api/interviews` | Interviews | create, list, upcoming, reminders, get, update, delete |
| `/api/notifications`, `/api/reports` | Notifications and reports | list, test, daily report |
| `/api/settings` | Settings | `GET`, `PUT /preferences`, `PUT /scoring`, `PUT /automation` |

Operational and configuration writes need `X-Admin-Token` (added by the web server for admin actions; fails closed
without `ADMIN_TOKEN`).

---

## 15. Running the application

Full steps: `README.md` → "How to run the application". In short (PowerShell, project folder):

```powershell
# first time
uv sync
Copy-Item .env.example .env          # set ADMIN_TOKEN and SECRET_KEY
cd web; npm install; npm run build; cd ..

# every time - two windows
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000      # window 1: API
cd web; npm start                                                  # window 2: dashboard
```

Open http://127.0.0.1:8502 (or your `WEB_PORT`). After changing Python code or a `config/*.yaml` file restart window 1;
after changing `web/` run `npm run build` and press Ctrl+F5.

---

## 16. Testing

```powershell
uv run pytest tests/unit -q          # no database needed
uv run pytest tests/safety -q        # rules: no scraping, no submit path in the worker, no token in the browser ...
uv run pytest tests/integration -q   # throw-away MongoDB database; skipped if MongoDB is unreachable
uv run pytest tests/e2e -q           # real browser against the built dashboard
cd web; npm test                     # front-end unit tests
```

Last full run (2026-11-23): unit 876 passed (1 expected failure), safety 253, integration 408, web 244, e2e 18 -
**1,799 passed, 0 failing**. Integration tests use mocked HTTP for job boards; e2e tests use local test forms. Mocked
tests prove the code's behaviour, not that a real external site works.

---

## 17. Current status: what is proven and what is not

**Working and checked against real data**

- Resume parsing and review on a real resume.
- Automatic discovery from 41 real company boards (live runs read every board without errors).
- Matching on 400+ real job postings; scores and eligibility re-checked after the JD-reading fixes.
- Instant scoring of imported jobs; LinkedIn table; Naukri bulk paste (parser tested on sample text).
- Job Matching, Job Discovery, Application Queue, Dashboard, Settings checked page by page in a real browser.
- Scheduler: daily discovery, scoring and queueing ran live; the worker prepared a real queued job into a manual
  handoff.

**Not proven**

- **Automatic submission on a real employer site** - only local test forms have been used. None of the configured
  companies allows it today (CAPTCHA / login), so every application is a manual handoff.
- **Naukri bulk paste on a real copied page** - tested on text in Naukri's usual layout only.
- **E-mail notifications** - optional and not configured.
- **Manual validation of the scoring** - no human sign-off has been recorded in `phase3_evaluation_report.md`.

---

## 18. Known limitations

- **Volume from automatic sources is small.** Only companies with a public Greenhouse or Lever board can be read, and
  few of their roles are in India. Most opportunities will come from LinkedIn and Naukri via paste.
- **Title filter is a text match.** A role is kept only if its title contains one of your keywords; add keywords in
  Settings if relevant roles are missed.
- **Location is a text match**, not a distance search; there is no "nearby cities" logic.
- **Posting dates** come from the job board; some boards report an update date rather than the first posting date.
- **A results-page paste has only a job summary**, so its score is rougher than one from the full description.
- **Responsibility matching is lexical** (shared skills and terms), not semantic.
- **Your stated experience is used as given.** If the profile says more years than the dates support, jobs asking for
  that many years will PASS when a recruiter would not agree - keep the figure accurate in Resume Manager.

---

## 19. Troubleshooting

| What you see | What to do |
|---|---|
| "Backend: unreachable" on Home | Start the API (window 1) or read its error output |
| "MongoDB: not connected" | Start the MongoDB service or fix `MONGODB_URI` in `.env` |
| The page looks old after an update | `cd web; npm run build`, then Ctrl+F5 |
| A config change (companies, preferences for the scheduler) has no effect | Restart the API |
| Discovery stores 0 new jobs | Jobs may already be stored (duplicates), filtered by title / location / age, or the daily cap is used; see the last run's notes on Job Discovery |
| Admin buttons are disabled | Set `ADMIN_TOKEN` in `.env` and restart both windows |
| The worker does nothing | It only prepares between 09:00 and 21:00 and only queued jobs (PASS, score ≥ 70) |
| A job shows a wrong requirement | Open Details on Job Matching to see the evidence sentence; report the wording so the parser can be fixed |

---

## 20. Glossary

| Term | Meaning |
|---|---|
| **Match score** | 0-100 fit between your profile and the job |
| **Eligibility** | PASS / FAIL / UNKNOWN check of the job's hard requirements, separate from the score |
| **Good match** | Fits your preferences, eligibility PASS, score ≥ the queue minimum (70) |
| **Fit preferences** | Matches your titles, locations, work mode and posting age (jobs you imported yourself always count) |
| **Manual handoff** | The app prepares everything; you apply on the site yourself |
| **Recorded by you** | An application you marked as applied; not confirmed by the employer's site |
| **Role fit** | Whether the job's function (e.g. engineering vs. sales) matches yours; a factor on the score |
| **Scoring version** | Version of the scoring rules; changes when weights or rules change, so old and new scores are not mixed |
| **Policy** | The per-source permission in `config/*_policies.yaml`; unknown means disabled |
