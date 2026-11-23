# syntax=docker/dockerfile:1.7
# AI JobPilot backend (API). The React dashboard has its own image: web/Dockerfile.
# No secret is ever copied or baked in: configuration comes from environment variables / the mounted config folder at RUN time.

# ---------------------------------------------------------------- builder: resolve dependencies from the lock file
FROM python:3.11-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install "uv==0.13.7"
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# --frozen: exactly the versions in uv.lock; --no-dev: no test tooling in production
RUN uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------- runtime
FROM python:3.11-slim AS runtime
ARG INSTALL_BROWSER=false
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}" \
    APP_ENV=production DEBUG=false \
    HOME=/home/app TZ=Asia/Kolkata

# a non-root user owns everything that is written at run time
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

# application code only (an allow-list: tests, docs, .env and local data are not part of the image)
COPY --chown=app:app ai ./ai
COPY --chown=app:app backend ./backend
COPY --chown=app:app browser ./browser
COPY --chown=app:app config ./config
COPY --chown=app:app database ./database
COPY --chown=app:app engines ./engines
COPY --chown=app:app integrations ./integrations
COPY --chown=app:app notifications ./notifications
COPY --chown=app:app scheduler ./scheduler
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app security ./security

RUN mkdir -p storage/resumes storage/generated storage/screenshots storage/notifications storage/browser_sessions logs config/backups \
 && chown -R app:app storage logs config

# Browser automation is OFF by default and the image does not contain a browser (smaller, fewer attack surface).
# Build with --build-arg INSTALL_BROWSER=true only if you have authorised sites and enable APPLICATION_AUTOMATION_ENABLED yourself.
RUN if [ "$INSTALL_BROWSER" = "true" ]; then playwright install --with-deps chromium && chown -R app:app /root/.cache 2>/dev/null || true; fi

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
