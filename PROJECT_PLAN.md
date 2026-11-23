# Project Plan

## Phase 1 – Project foundation  ✅ implemented and verified
FastAPI + Streamlit + MongoDB connectivity, Pydantic settings, logging, health endpoints, tests.

## Phase 2 – Resume intelligence  ✅ implemented (see README "Phase 2" for details and limits)
Objective: make the system understand the candidate's professional profile.

- Master resume upload (PDF / DOCX) with content validation, size limit, safe server-side filenames.
- Text extraction (PyMuPDF, python-docx), cleaning, section detection.
- Rule-based extraction of contact info, summary, skills, experience, projects, achievements,
  education and certifications; optional AI-assisted fill (off by default).
- Structured candidate profile (`ai/schemas/candidate_profile.py`) with provenance, review flags,
  missing-field list and factuality validation (`ai/validators/factuality_validator.py`).
- MongoDB persistence: `resumes`, `candidate_profiles`, `profile_versions`, `users`.
- REST API under `/api/resumes` and the Streamlit Resume Manager page for review and correction.
- Resume versions, active-resume selection, profile version history.

Completion criteria: resume information is extracted and can be reviewed and corrected.

## Phase 3 – AI matching engine  ✅ implemented; manual validation of the 30-JD report PENDING human sign-off
Deterministic, explainable matching of the verified candidate profile against job descriptions.

- JD parser (rules, optional Claude refinement), skill dictionary (`config/skills.yaml`), hard eligibility checker,
  weighted scorer (`config/scoring_weights.yaml`), explainability, versioned/hashed match reports.
- `job_matches` + `jobs` collections, `/api/matching/*` endpoints, Streamlit **Job Matching** page.
- 30 synthetic JDs + evaluation report (`docs/phase3_evaluation_report.md`) awaiting human review.

## Phase 4 – Job discovery  ✅ implemented; platform policies need human confirmation
Permitted-source discovery, normalisation, duplicate detection and storage; policy registry (fail closed).

- LinkedIn / Indeed: manual import only. Naukri: automation disabled pending policy review (manual import allowed).
- Company career pages: Greenhouse + Lever public read APIs and approved JSON-LD pages for companies listed in `config/career_sources.yaml`.
- `/api/jobs/*` endpoints, Streamlit **Job Discovery** page, `jobs` + `discovery_runs` collections.
- Live-verified: Lever public API (demo site). Greenhouse, generic pages: tested with mocks only.

## Phase 5 – Document generation  ✅ implemented; real-resume quality needs your review
Job-specific resume, cover letter and screening answers from the verified profile, with factuality validation, version management,
explicit approval and DOCX/PDF export.

- Deterministic writers (always available) + optional Claude wording that must pass the same fact check.
- `/api/documents/*` endpoints, Streamlit **Document Generation** page, `documents` collection.
- Verified with synthetic data and the real upload -> review -> generate -> approve -> export flow; not yet reviewed on your own resume/jobs.

## Phase 6 – Application execution  ✅ implemented; verified on LOCAL forms only
Policy-gated browser automation (Playwright), form detection/mapping, approved-document upload, explicit approval, evidence-based verification,
duplicate prevention, manual handoff, `/api/applications/*`, **Application Queue** and **Applied Jobs** pages.
No real third-party site has been automated; default configuration automates nothing.

## Phase 7 – Scheduler, queue and notifications  ✅ implemented; verified with mocked services and a local restart test
Daily discovery scheduler, persistent application queue, preparation worker (never submits), daily limits, retry/duplicate protection, optional Gmail notifications,
daily reports, token-protected APIs, Dashboard/Automation pages. Gmail was NOT verified live (no credentials).

## Phase 8 - Dashboard  ✅ implemented; verified with AppTest on a real database and live HTTP
All nine sections (Dashboard, Job Discovery, Resume Manager, Job Matching, Application Queue, Applied Jobs, Interview Tracker, Analytics, Settings) on real data; new backend: interviews, analytics, settings,
application notes/reject, job paging and filters. Not verified in a visual browser session (AppTest + a booted Streamlit server only).

## Later phases (not started)
Analytics.

## Frontend migration (after Phase 9): Streamlit -> React

The Streamlit dashboard was replaced by a React + TypeScript app in `web/` (Vite), served by a small Node server that proxies `/api` to the backend and keeps `ADMIN_TOKEN` server-side.
All 11 pages were ported with the same safety rules. Earlier phase notes above that mention Streamlit describe the historical implementation. Verification: `cd web; npm test`, `tests/safety/test_web_frontend.py`, `tests/e2e/test_react_playwright.py`.
