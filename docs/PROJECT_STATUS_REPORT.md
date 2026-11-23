# AI JobPilot - Completion Audit against the "Fully Automated Job Application Agent" Goal

Date: 2026-11-13. Method: code and config inspection, plus **live checks against the running API/MongoDB/dashboard** and the automated test suites run during this work.
Status labels: **Completed** = implemented AND verified; **Partial** = implemented but not fully functional or not verified live; **Pending** = not implemented; **Failed** = implemented but not working.
Nothing is marked Completed because code exists.

## 0. Verdict

AI JobPilot is a working **assisted** job-application system: it reads a resume, finds jobs from permitted public sources, scores and explains eligibility, prepares customised documents, fills forms on a controlled test form, and
requires the candidate's explicit approval before any submission. It is **not** a fully automated agent, and **two parts of the stated goal cannot be built as written**:

1. **Automated search and application on LinkedIn, Naukri and Indeed.** Indeed's terms explicitly prohibit automation of applications; LinkedIn prohibits scraping and bots; Naukri is reported to. The project policy files set all three to
   manual handoff, and the project rules forbid scraping, automated login and anti-bot evasion. The goal itself says the system must "respect platform restrictions and applicable automation policies", so the goal contradicts itself on these platforms.
2. **Submitting without approval.** The project requirement (Phase 6) is that every submission needs an explicit candidate approval bound to the exact application. There is no code path that submits without it.

What can be automated is described in section 7.

## 1. Status by workflow step

| # | Step | Status | Evidence |
|---|---|---|---|
| 1 | Resume intelligence (upload, parse, profile) | **Completed** (with one real-data defect found and fixed today) | 53 extraction tests; live re-parse of the real resume: 0 -> 4 jobs, 0 -> 7 projects, 43 skills, 3 education, 12 certifications. Defects found on the real resume and fixed: unrecognised headings ("RELEVANT WORK EXPERIENCE", "SELF PROJECTS"), job title on the line after the company, bullet-style projects. Other resume layouts have not been tested with real files. |
| 2a | Job discovery: company career pages (Greenhouse) | **Completed** | Live on 2026-11-14 after the size-limit fix: **12 of 12** configured boards read in one run, 0 errors, 12 requests; 4,612 postings listed, 200 passed the title filter, 88 relevant jobs stored (11 companies). |
| 2b | Job discovery: Lever | **Completed** (reader) | Live read-only check on 2026-11-14 against one public Lever board: 302 postings parsed, all with title, link and description; nothing stored. No Lever company is in your `career_sources.yaml` yet. Policy note: documentation confirmed via secondary sources only. |
| 2c | Job discovery: Workday | **Pending** | Policy status PENDING_POLICY_REVIEW; no extractor enabled. |
| 2d | Job discovery: generic company pages | **Partial** | Mocked tests only; requires per-site approval; never run live. |
| 2e | Job discovery: LinkedIn / Indeed | **Pending by policy** | MANUAL_ONLY. Manual import exists and works (tested); a LinkedIn search-link builder and quick-import form were added today (tested, browser-checked). No automated search exists or can exist. |
| 2f | Job discovery: Naukri | **Pending by policy** | PENDING_POLICY_REVIEW; manual import only. |
| 2g | Filters: title, skills, location, work mode, salary, experience, companies, posted date | **Completed** (as filters) | Implemented in the discovery request and job list; exercised live (title + location filters). **Search radius / "nearby locations": Pending** - location is a text match only; no distance or nearby-city logic exists. |
| 3 | JD analysis (mandatory/preferred skills, experience, education) | **Completed** | Live: 74 real JDs parsed and scored. Strict by design (see limitations). |
| 4 | Resume-to-JD scoring and ranking | **Completed** (engine) / **Partial** (validation) | Live: 74 jobs scored twice (profile v3 and v5). Score and eligibility are separate by design (your example table merges them). **Phase 3 human validation: NOT DONE** (docs/phase3_evaluation_report.md says so). Scoring has never been validated by a person against real hiring outcomes. |
| 5 | Automated application | **Partial** | The engine opens a form, fills it from the verified profile, uploads documents, shows a review, and submits **after explicit approval**; verified end to end **only on local dummy forms** (browser test: exactly one POST, evidence-based verification). **No real site has ever been tested**, by rule. LinkedIn Easy Apply, Naukri, Indeed: manual handoff only. Greenhouse/Lever/generic: AUTHORIZATION_REQUIRED (you must list authorised sites). |
| 6 | Location-based prioritisation | **Partial** | Location check in eligibility (text-based; UNKNOWN when relocation preference is empty - 55 of 74 jobs). No radius. |
| 7 | Tracking dashboard | **Completed** | 12 pages in React; live against the real database; dashboard numbers asserted equal to DB counts in a browser test; all 12 pages audited live with no errors. Company-wise application history exists in Applied Jobs; interview updates are manual entries. |
| 8 | Scheduled automation | **Partial** | Scheduler, queue, retries, daily limits, daily report implemented and tested with a fake clock and mocked sources. **Never started on the real system** (scheduler disabled, no ADMIN_TOKEN, no Gmail). The worker can only prepare, never submit (by design, tested). |

## 2. Answers to the twelve questions

1. **Fully implemented:** resume parsing, JD parsing, matching and eligibility, Greenhouse discovery, manual import, document generation (tested), application preparation and approval workflow (on local forms), duplicate protection, dashboard and API.
2. **Partially implemented:** Lever and generic career-page discovery, automated application (local forms only), location filtering (no radius), scheduler (not run live), e-mail notifications (not configured).
3. **Not implemented:** automated discovery on LinkedIn/Naukri/Indeed, Workday, search radius, any submission without approval (by design), Easy Apply automation (by policy).
4. **APIs and services working:** 95 operations in 15 groups. **All 28 parameter-free GET endpoints return 200 on the live API.** Write endpoints verified live today: resume re-parse, job discovery, job scoring, manual import path (tests). Others verified in tests on a throwaway database.
5. **Integrations connected:** MongoDB (live), Greenhouse public API (live, 6 of 12 boards), Playwright with Edge (local forms only). **Not connected / not run:** Lever, Workday, generic pages, LinkedIn, Indeed, Naukri, Gmail, Anthropic (no key), Docker (not installed).
6. **Resume parsing correct?** Mostly, after fixes. Skills, education, certifications, 4 jobs, 7 projects, 4.2 years were extracted from the real resume. Total experience is calculated (flagged for review). The new profile is pending your review.
7. **JD analysis and matching functional?** Yes (live, 74 jobs). Limitation: mandatory skills must be written in the verified profile; related skills do not count. After the re-parse: 7 of 74 jobs pass eligibility at 4 of 6 companies; 49 fail on minimum experience.
8. **Discovery across LinkedIn/Naukri/Indeed/company pages?** Company pages: yes via Greenhouse (partial). LinkedIn/Naukri/Indeed: no automated discovery, manual only.
9. **Is the engine actually submitting applications?** On local test forms, yes, exactly once, after approval. On real sites: **no, never, and it has never been tested on one.**
10. **Dashboard connected to real records?** Yes (live, 74 jobs, 148 stored matches).
11. **Scheduler and background automation functional?** Implemented and tested with fakes; not running live; the worker cannot submit.
12. **All modules tested end to end?** No. See section 5.

## 3. Bugs and limitations found

Fixed during this work: resume heading synonyms; job-title and bullet-project layouts; interview form cleared typed input on a server error; Application Queue job selection reset when an application changed state; salary field silently blocked a form (native validation); scheduler lock check used real time instead of the scheduler clock (test-date time bomb); dev proxy depended on a port used by another project; React Router warnings.

Fixed on 2026-11-14: large Greenhouse/Lever boards were skipped ("Response too large") - the limit is now set per source in `platform_policies.yaml` (40 MB for the two documented JSON APIs, 5 MB default, 60 MB ceiling);
the per-run job cap was applied before the title filter, so one large board could use the whole allowance - the title filter now runs first; five web packages with known vulnerabilities were updated
(express, http-proxy-middleware, react-router-dom, vite, vitest).

Open:
- Strict mandatory-skill matching can mark a qualified candidate FAIL when the exact skill word is not in the profile.
- Scoring was not validated by a human (Phase 3 report).
- Location is a text match; no radius; empty preferences leave location UNKNOWN.
- Total experience years are inferred from internship dates and flagged.
- Skills a candidate has but did not write (for example Statistics) are never added automatically.
- Docker files are statically checked only; never built or started.
- Dependency vulnerability audit not completed (no network to PyPI at the time).
- Code coverage not measured (the run was stopped for low memory).
- The full Python regression was last completed before the final parser, scheduler-route and web changes (1432 passed); a later regression run was stopped for low memory. Targeted suites after those edits passed.
- Written 2026-11-17: DEPLOYMENT.md, USER_GUIDE.md, TESTING_REPORT.md, SECURITY.md (in `docs/`). The test results in section 5 are superseded by `docs/TESTING_REPORT.md`.

## 4. Missing APIs / integrations

- No search-radius or nearby-location logic.
- No live-verified Lever, Workday or generic-page connector.
- No Naukri, Indeed or LinkedIn discovery or application connector (and none permitted).
- No Gmail setup; no AI key (AI wording, optional).
- No application-outcome feedback source other than what the candidate records.

## 5. Testing evidence

| Suite | Result | Notes |
|---|---|---|
| Python (unit, integration, safety, database, duplicates, errors, Phase 6 browser on local forms) | 1432 passed, 0 failed, 1 xfailed | Earlier today, before the last edits |
| Resume extraction after the parser fix | 53 passed | Includes tests for the layouts found on the real resume |
| Web unit and component (vitest) | 212 passed, 24 files | Mocked backend |
| React browser tests (real API, Node server, Edge, local dummy form) | 18 of 18 passed, twice | Throwaway database; no real site |
| Web safety scan, Docker/config static checks | 99 and 19 passed | Docker not built |
| Live audit of the running system | 12 of 12 pages clean; 28 of 28 GET endpoints 200 | Real database |

Not tested: any real job site, Lever live, Docker, e-mail, AI mode, the scheduler on the real system, a human review of match quality.

## 6. What "duplicate-safe, verifiable" means today (verified)

Duplicate applications are prevented by idempotency keys and unique indexes; an uncertain submission is never retried automatically; the event log is append-only and PII-free; SUBMITTED/VERIFIED require evidence. Tested in the duplicate-application suite (13 scenarios) and the browser tests against a local form that counts POSTs.

## 7. What can be made automatic within the rules, and remaining work

Priority order:
1. **Finish Phase 9 deliverables:** run the full regression, coverage and dependency audit; write the four missing docs; build the Docker images on a machine with Docker.
2. **Daily autopilot up to the approval step:** discovery -> scoring -> prepare documents and form -> notify -> one-click approve. Needs ADMIN_TOKEN, preferences and thresholds set, scheduler started (Automation page).
3. **Raise coverage of real jobs:** fix the response-size limit for large Greenhouse boards (code change), add Lever boards, add more companies; test Lever live.
4. **Authorised ATS applying:** for Greenhouse/Lever/generic pages only, after YOU verify each site's terms and list it under `authorized_sites`; first test on a site that offers a sandbox or a test posting; every submission still needs your approval.
5. **Matching quality:** human review and sign-off of the Phase 3 evaluation; decide whether to relax strict mandatory-skill matching (a deliberate product decision, with a scoring_version bump and before/after).
6. **Search radius / nearby locations:** new feature.
7. **Not recommended and not buildable within the stated rules:** automated search or application on LinkedIn, Indeed or Naukri, and submission without approval.
