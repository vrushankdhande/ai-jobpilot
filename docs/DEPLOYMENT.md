# Deployment

Two ways to run AI JobPilot: directly on Windows (verified), or with Docker Compose (**not verified**: Docker is not
installed on the development machine, so the images have never been built or started; only static checks of the
Docker files ran).

## 1. Local (Windows, PowerShell) - verified

Prerequisites: Python 3.11 through `uv`, MongoDB on `localhost:27017`, Node.js 22+.

```powershell
# one time
uv sync                                  # if Playwright stalls: uv sync --no-install-package playwright
Copy-Item .env.example .env              # then edit .env (see section 3)
cd web; npm install; npm run build; cd ..

# terminal 1 - API
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000

# terminal 2 - dashboard  ->  http://127.0.0.1:8502
cd web; npm start
```

Checks:

```powershell
uv run python scripts/check_environment.py     # Python, MongoDB, config files
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing
```

For front-end development use `cd web; npm run dev` (http://localhost:5173). It contains the same `/api` proxy, so
no second server is needed. Restart it after changing `.env` or `web/vite.config.ts`.

## 2. Docker Compose - NOT verified

```powershell
Copy-Item .env.example .env     # fill in MONGO_ROOT_PASSWORD, ADMIN_TOKEN (24+ chars), SECRET_KEY (16+ chars)
docker compose build
docker compose up -d            # dashboard: http://localhost:8501
```

- Three services: `mongo` (authenticated, not published), `backend` (not published), `web` (published on
  `127.0.0.1:8501` only).
- Compose refuses to start when a required secret is missing. Secrets come from `.env` at run time and are not
  copied into an image (`.dockerignore` excludes `.env`, `storage/`, `logs/`).
- `./config` is mounted so policies, limits and preferences can be edited without rebuilding.
- Before exposing the dashboard beyond this computer, put a reverse proxy with authentication and TLS in front of
  it. The dashboard itself has no login.

The first real build may reveal problems the static checks cannot find. Treat the first run as a test.

## 3. Configuration

All settings are read from `.env` (see `.env.example` for every key and its meaning).

| Key | Needed for | Note |
|---|---|---|
| `MONGODB_URI`, `MONGODB_DATABASE` | everything | local default works |
| `ADMIN_TOKEN` | any button that changes state (approve, scheduler, settings) | empty = dashboard is read-only. Generate: `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `SECRET_KEY` | encrypted browser sessions, Gmail credentials, encrypted backups | required in production |
| `APP_ENV=production` | production | the API refuses to start without `ADMIN_TOKEN` (24+) and `SECRET_KEY` |
| `SCHEDULER_AUTOSTART` | daily automation without pressing Start | default `false` |
| `ANTHROPIC_API_KEY` + consent flags | optional AI wording | off by default; the system works without it |
| `APPLICATION_AUTOMATION_ENABLED` | form filling on authorised sites | default `false` |

Behaviour files in `config/` (edit deliberately):

- `career_sources.yaml` - companies whose public boards may be read.
- `platform_policies.yaml` - which sources may be read automatically (fail closed).
- `application_policies.yaml` - which sites may be filled automatically (default: local test forms only).
- `job_preferences.yaml`, `scoring_weights.yaml`, `automation_limits.yaml`, `skills.yaml`.

## 4. Turning on the daily automation

1. Set `ADMIN_TOKEN` in `.env`; restart the API and the web server.
2. Review and confirm your profile in **Resume Manager** (matching in the scheduler needs a reviewed profile).
3. Fill **Settings -> Preferences** (titles, locations, work mode).
4. Open **Automation** and press **Start** (or set `SCHEDULER_AUTOSTART=true`).

Each day it discovers jobs from permitted sources, scores them, queues good matches and prepares the applications.
It never approves or submits: prepared applications wait in **Application Queue** for your decision.

## 5. Backup and restore

```powershell
uv run python scripts/db_tools.py validate
uv run python scripts/db_tools.py backup  --to backups/2026-11-14 --encrypt
uv run python scripts/db_tools.py verify  --from backups/2026-11-14
uv run python scripts/db_tools.py restore --from backups/2026-11-14 --target-db ai_jobpilot_restored
```

Restore never defaults to the live database. Also back up `storage/` (uploaded resumes, generated documents) and
`config/`.

## 6. Updating

```powershell
uv sync
cd web; npm install; npm run build; cd ..
uv run pytest tests/unit -q          # quick check before restarting
```

Then restart the API and the web server.

## 7. Known deployment gaps

- Docker images never built or run.
- No dependency vulnerability audit result for the Python packages (see `TESTING_REPORT.md`).
- The dashboard has no user login; it relies on being bound to `127.0.0.1`.
- E-mail notifications were never configured or sent.
