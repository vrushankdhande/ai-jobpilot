# Testing Report

Date: 2026-11-17. Every number below comes from a test run executed during this work. Nothing here is estimated.
No real job application was submitted or attempted at any point; no real job site was written to.

## Results

| Suite | Result | Notes |
|---|---|---|
| Python unit tests (`tests/unit`) | 804 passed, 1 expected failure | run 2026-11-14, before the final discovery-stats edit; the adapter file (83 tests) was re-run after it and passed |
| Python integration (`tests/integration`, `tests/test_*.py`) | 407 passed, 3 failed -> all 3 fixed | the three files holding the failures were re-run afterwards: discovery flow, Phase 8 backend, scheduler flow (223 + 43 passed) |
| Python safety (`tests/safety`) | 241 passed | includes the web-bundle secret scan and production-config checks |
| React unit and component (vitest) | 212 passed (24 files) | after the web package updates |
| React browser end-to-end (`tests/e2e`) | 18 passed | real API, Node server, Edge, local dummy form, throwaway database |
| Web build and typecheck | passed | after the web package updates |
| Dependency audit, Python | no known vulnerabilities (46 packages) | `pip-audit` against the locked runtime set |
| Dependency audit, web | **not clean** | see below |

The integration, unit and safety batches were run **separately**, in smaller groups because of low memory. They were not
re-run as one combined suite after the last edits.

## The 3 integration failures

All three were test or side-effect problems, not product defects:

- **Two date-dependent tests** (analytics overview, daily report): the tests built data for the fixed day 2026-11-13, but the
  repositories stamp the real current time on update, so the tests only passed on that exact date. The tests now write the
  fixed timestamps directly. (The same cause had been fixed once before for the scheduler lock check; a similar time bomb
  may exist elsewhere in the tests.)
- **Discovery stats test:** my change to filter by title before the per-run job cap meant postings dropped early were no longer
  counted as "filtered out". The adapter now reports how many it dropped and the run totals include them.

## Live checks against public APIs (read-only)

| Check | Result |
|---|---|
| Greenhouse public API, 12 configured companies, one run | 12 of 12 boards read, 0 errors, 12 requests, 88 relevant jobs stored |
| Lever public API, one board | 302 postings parsed, all with title, link and description; nothing stored |
| Live dashboard audit (12 pages) and 28 parameter-free GET endpoints | clean / all 200 (earlier session) |

## Not done / not clean

- **Web dependency advisories remain** (dev tooling mostly): the dev build tools (vite, vitest and their helpers) and react-router
  have advisories whose fixes are major-version upgrades. Runtime packages express, http-proxy-middleware and
  react-router-dom were raised to patched versions within their lines; `npm audit --omit=dev` still reports 2 moderate
  (react-router, fix needs v7). The dev-tool advisories affect only the developer machine (dev server, test runner), not the
  served app. A major upgrade was not attempted, to avoid breaking a working app without being asked.
- **Code coverage not measured** (the run was stopped earlier for low memory and not repeated).
- **Docker images never built or started** (Docker not installed on this machine).
- **Not tested at all:** any real job site, e-mail sending, AI mode with a real key, the scheduler running on the real system,
  a human review of match quality (Phase 3).
- **Scoring was not validated by a person** (see `docs/phase3_evaluation_report.md`).

## How to re-run

```powershell
uv run pytest tests/unit -q
uv run pytest tests/integration -q          # about 16 minutes; needs MongoDB
uv run pytest tests/safety tests/e2e -q     # e2e needs Node and Edge
cd web; npm test; npm run build
```
