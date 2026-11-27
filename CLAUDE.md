# CLAUDE.md

Guidance for working on AI JobPilot.

## Environment
- Python 3.11 only (`.python-version`, `requires-python = ">=3.11,<3.12"`), managed with `uv`.
- Commands: `uv sync`, `uv run pytest -v`,
  `uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload`,
  web dashboard: `cd web; npm install; npm run build; npm start` (Node 22+, http://127.0.0.1:8502; or `npm run dev` on http://localhost:5173 with the proxy built into Vite); front-end tests: `cd web; npm test`.
- Windows/PowerShell-compatible commands only.
- Note: `playwright` (a future-phase dependency) can stall during download on this machine; if so use
  `uv sync --no-install-package playwright` and `uv run --no-sync ...`.

## Architecture
- `config/settings.py` – single source of configuration (`get_settings()`). No scattered `os.getenv`.
- `database/connection.py` – `DatabaseManager`, one shared MongoClient (tz-aware); `get_database_manager()` is the FastAPI dependency.
- `database/repositories/` – all MongoDB access (`ResumesRepository`, `UsersRepository`). `database/indexes.py` creates indexes at startup.
- `backend/main.py` – app factory + lifespan; routers in `backend/api/routes/` (`health.py`, `resumes.py`).
- `backend/services/resume_service.py` – resume workflow (upload, parse, correct, version, delete). Routes stay thin.
- `engines/resume/` – `document_validator.py` (upload checks), `resume_parser.py` (PDF/DOCX text + pipeline),
  `profile_extractor.py` (rule-based extraction), `skills_taxonomy.py` (lookup table, not candidate data),
  `ai_extractor.py` (optional LLM layer).
- `ai/schemas/candidate_profile.py` – Pydantic profile models; `ai/validators/factuality_validator.py` – fact-checks a profile against the source text.
- `web/` – the React (Vite + TypeScript) dashboard; it replaced Streamlit. It talks to the backend over HTTP only (never MongoDB), via `web/src/api.ts` (the only place that calls `fetch`). `web/server/server.mjs` serves `web/dist` and proxies `/api`:
  it adds `X-Admin-Token` itself, only for calls the browser marks `X-Admin-Action: 1`, and strips any browser-supplied token/cookie. The browser bundle must never contain or read `ADMIN_TOKEN` (safety test `tests/safety/test_web_frontend.py`, e2e `tests/e2e/test_react_playwright.py`).
  Pure display logic lives in `web/src/lib/*.ts` with vitest tests; pages are `web/src/pages/*.tsx`.
- `engines/matching/` (Phase 3): `jd_parser.py`, `skill_extractor.py` (catalog from `config/skills.yaml`), `candidate_view.py`,
  `skill_analysis.py`, `eligibility_checker.py`, `match_scorer.py`, `explainability.py`, `matching_engine.py` (pipeline),
  `evaluation.py`. `ai/claude_client.py` is the only AI-provider integration (Anthropic `ClaudeClient` and Google `GeminiClient`, chosen by `AI_PROVIDER`; same `complete_json` contract, consent flags and fallbacks; `new_ai_client`/`ai_status` pick the provider; tests pin AI off in `tests/conftest.py`). `backend/services/matching_service.py` +
  `backend/api/routes/matching.py` + `database/repositories/{jobs,matches}_repository.py` persist/serve it.
- Discovery (Phase 4): `integrations/policy.py` (registry, fail closed), `integrations/base_adapter.py` (`JobAdapter`, `PolicyHttp`,
  `RateLimiter`, `ManualOnlyAdapter`), `integrations/{linkedin,indeed,naukri}/adapter.py` (manual import only),
  `integrations/career_pages/` (`ats_detector.py` = detection only; `providers/` = extraction), `integrations/registry.py`,
  `engines/discovery/{job_normalizer,deduplication,discovery_engine}.py`, `database/models/job.py`,
  `database/repositories/{jobs,discovery_runs}_repository.py`, `backend/services/discovery_service.py`, `backend/api/routes/jobs.py`.
- Documents (Phase 5): `engines/resume/resume_builder.py` (customised resume), `engines/generation/{resume_customizer,fact_pack,cover_letter,
  screening_answers,document_engine,ai_writer,render,export_docx,export_pdf,exporters}.py`, `ai/schemas/document_schema.py`,
  `ai/validators/document_validator.py` (+ re-export in `factuality_validator.py`), `database/repositories/documents_repository.py`,
  `backend/services/document_service.py`, `backend/api/routes/documents.py`, `web/src/pages/DocumentGeneration.tsx`.
- Applications (Phase 6): `browser/{browser_manager,session_manager,locator_engine,safety_checks,screenshot_manager}.py`, `engines/application/{state_machine,form_mapper,
  document_uploader,document_validator,submission_verifier,retry_manager,application_engine}.py`, `integrations/{application_policy,application_adapters}.py` (+ `ApplicationAdapter`
  in `base_adapter.py`), `config/application_policies.yaml`, `database/{models/application,repositories/applications_repository}.py`, `security/*`,
  `backend/services/application_service.py`, `backend/api/routes/applications.py`, `web/src/pages/{ApplicationQueue,AppliedJobs}.tsx`.
- Scheduler (Phase 7): `scheduler/{scheduler,application_queue,application_worker,daily_discovery,daily_report,runtime}.py`, `notifications/{email_notifications,notification_templates}.py`,
  `database/repositories/{scheduler,queue,notifications}_repository.py`, `backend/api/routes/scheduler.py`, `web/src/pages/{Dashboard,Automation}.tsx`, `config/automation_config.py`.
- Dashboard (Phase 8, now React): `web/src/pages/*.tsx`, shared `web/src/components/{ui,Layout}.tsx`, `web/src/{api,hooks,format}.ts`, `web/src/lib/*`;
  `backend/services/{analytics_service,settings_service}.py`, `backend/api/routes/{analytics,interviews,settings}.py`, `database/{models/interview,repositories/interviews_repository}.py`, `config/yaml_edit.py`.
- Sign-in / separate data per person: `config/tenant.py` (`current_tenant` ContextVar), `backend/services/auth_service.py` (scrypt hashes, HttpOnly `jp_session` cookie, owner = first account),
  `backend/api/routes/auth.py`, `auth_middleware` + `require_owner` in `backend/api/security.py`, `web/src/auth.tsx`, `web/src/pages/Account.tsx`. Each non-owner person has their own database
  (`<MONGODB_DATABASE>_u_<id>`) and `storage/users/<id>/`; `get_settings()` and `DatabaseManager.get_database()` follow the signed-in person, so repositories need no user filter.
  Code without a request (scheduler threads, scripts) has no tenant = the owner's data; per-person schedulers are keyed in `scheduler._holder`. Never read another person's data through the base database.
  Shared files (scoring weights, automation caps, .env) are owner-only. Tests run with `AUTH_ENABLED=false` (conftest); `tests/integration/test_auth.py` covers sign-in and isolation.
- Config: `config/scoring_weights.yaml`, `job_preferences.yaml`, `skills.yaml` via `config/scoring_config.py` (nothing hard-coded).
- Future-phase packages (`integrations/`, `browser/`, `scheduler/`, ...) exist as stubs; build on them, don't restructure.

## Conventions
- Never fabricate or silently infer candidate data. Missing stays `None`/empty and is listed in `missing_fields`;
  anything calculated/inferred is tagged in `provenance` (`kind: inferred`) and added to `review_flags`.
- No artificial confidence scores (`confidence_score` stays `None`).
- Uploaded files: validate content (not just extension/MIME), store under server-generated names in `storage/resumes/`,
  never overwrite, never expose paths/URLs. Original filename is display-only and sanitised.
- Never log resume content or personal data; log ids, types and sizes only.
- AI extraction is opt-in (`AI_EXTRACTION_ENABLED` + API key). It only fills empty fields and every value must appear in the source text.
  Treat resume text as untrusted data in prompts.
- Edits to a profile create a new `profile_version` and archive the old one in `profile_versions`; re-parsing needs `force=true`.
- Matching: the deterministic engine is the source of truth for scores/eligibility. Claude may only (a) re-classify skills that
  appear in the JD text and (b) word a summary; it is opt-in (key + enable flag + consent), has a deterministic fallback,
  and must never receive resume PII. UNKNOWN is never PASS and never 0. Match score and eligibility are always separate.
  Don't tune weights to improve the evaluation dataset; bump `scoring_version` and show before/after if weights change.
  Scoring 1.1 added `engines/matching/role_fit.py` (job function vs candidate function, a stored and explained factor on the score), near-equivalent skills meeting mandatory skills
  (`eligibility.mandatory_skill_equivalent_credit`) and JD-reading fixes; the evidence and before/after are in `docs/scoring_methodology.md`. Test JD-reading changes against real postings,
  not only the labelled dataset (`tests/unit/test_jd_reading_fixes.py` holds the generic wording cases).
  Never claim manual validation without a human sign-off in `docs/phase3_evaluation_report.md`.
- Discovery: never add scraping, automated login, CAPTCHA/anti-bot/rate-limit evasion, proxies or header tricks. A new source needs a
  policy entry first (`platform_policies.yaml`, default DISABLED) and an authorised method; unknown permission = disabled. Never
  fetch a user-supplied URL except through an allow-listed provider API. Tests use mocked HTTP and the `no_network` fixture; a live
  call is only acceptable against a documented public API and must be reported as such. Never claim jobs were collected from a
  platform unless a permitted live test actually succeeded. Uncertain duplicates are flagged for review, never auto-merged.
- Documents: only verified profile facts, never fabricated. Bullets are verbatim; skills are reordered, never added; missing personal
  details (notice, salary, relocation, work authorisation) are `needs_input` placeholders, never guessed. Claude may only word text from
  the fact pack (no contact details), needs `AI_DOCUMENTS_CONSENT`, and its output must pass the validator or is discarded. Every edit or
  regeneration is a new version; approval is an explicit candidate action on validated content; exports of drafts are marked DRAFT; the
  master resume is never modified. If a new claim type is generated, add a validator check for it first.
- Applications: automation is closed by default and only for sites allowed in `application_policies.yaml`; restricted platforms are manual handoff. Never solve/bypass
  CAPTCHAs, log in, store credentials, spoof or evade. Never submit without a backend-recorded approval bound to the plan hash; the click is preceded by the never-retry
  marker; SUBMITTED/VERIFIED only on evidence; uncertain = VERIFICATION_PENDING, never auto-retried; events are append-only and PII-free. Tests use only local forms.
  Don't claim any real platform works: only local forms were exercised.
- Scheduler/queue: the worker only calls Phase 6 `prepare` - it has no code path to approve or submit (a safety test scans for it). Retries are bounded and only after checking the application\n  cannot have been sent; uncertain = needs_verification. Daily limits are atomic counters; YAML limits are hard caps (dashboard can only lower). Operational endpoints need `X-Admin-Token`\n  (fail closed). Gmail credentials only Fernet-encrypted; e-mail is optional and never blocks the pipeline. Reports count only evidence-backed submissions.\n- Dashboard: pages call the API only through `web/src/api.ts` (no pymongo/database imports and no direct `fetch` - a test scans for it); metrics come from stored records, empty = empty state, never sample data. Settings writes are validated by the
  app's own loaders before replacing a file, keep YAML comments, bump scoring_version on scoring changes, and are limited to non-secret keys; secrets are never displayed or editable. Operational/config writes need `X-Admin-Token`.
  UI rule: a form that the server can reject must not clear what the user typed (clear only after a successful save); job/option labels in a selector must not change when state changes (the selection is kept in the URL).
- Database access via dependency injection so tests can override it.
- Tests: unit tests need no DB; integration/safety tests use a throw-away database on the configured MongoDB and skip if unreachable.
- Don't report a component as working unless it was actually run. Mocked tests are not proof of real integrations.
- Preserve existing files; change only what a phase needs.
