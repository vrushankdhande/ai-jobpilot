"""Shared FastAPI security dependencies and HTTP hardening middleware."""
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from config.settings import Settings, base_settings, get_settings
from config.tenant import Tenant, current_tenant

MAX_BODY_BYTES = 30 * 1024 * 1024        # uploads are limited to MAX_UPLOAD_MB (default 10) by the upload route; nothing legitimate is bigger than this


def require_admin_if_configured(x_admin_token: Annotated[str | None, Header()] = None, settings: Settings = Depends(get_settings)) -> None:
    """For the irreversible application actions (approve / submit): when ADMIN_TOKEN is configured it must be supplied (constant-time compare).
    Production refuses to start without a token, so there the check is always on; in development without a token the dashboard works unchanged."""
    if not settings.admin_token:
        return
    if not x_admin_token or not secrets.compare_digest(x_admin_token.encode(), settings.admin_token.encode()):
        raise HTTPException(401, "Missing or invalid admin token.")


SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Cross-Origin-Resource-Policy": "same-origin"}


async def hardening_middleware(request: Request, call_next):
    """Reject oversized bodies up front and add defensive headers; API responses are never cached."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        resp = JSONResponse(status_code=413, content={"detail": "Request body too large."})
    else:
        resp = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    if request.url.path.startswith("/api/") or request.url.path.startswith("/health"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# ---- sign-in ------------------------------------------------------------------------------------------------------
PUBLIC_API = ("/api/auth/status", "/api/auth/login", "/api/auth/setup", "/api/auth/logout")
OWNER_FALLBACK = Tenant(user_id="0" * 32, name="Owner", role="owner")     # logins switched off (tests): one person, the owner


async def auth_middleware(request: Request, call_next):
    """Every /api call needs a signed-in person (except signing in itself). It then runs as that person: their own database and folders
    (config/tenant.py). No session = 401, nothing is read."""
    settings = base_settings()
    path = request.url.path
    if not settings.auth_enabled or not path.startswith("/api/") or path in PUBLIC_API:
        return await call_next(request)
    from starlette.concurrency import run_in_threadpool

    from backend.services.auth_service import COOKIE, AuthService
    from database.connection import DatabaseConnectionError, get_database_manager
    token = request.cookies.get(COOKIE)
    tenant = None
    if token:
        try:
            manager = request.app.dependency_overrides.get(get_database_manager, get_database_manager)()
            tenant = await run_in_threadpool(lambda: AuthService(manager.base_database(), settings).tenant_for(token))
        except DatabaseConnectionError:
            return JSONResponse(status_code=503, content={"detail": "Database unavailable"})
    if tenant is None:
        return JSONResponse(status_code=401, content={"detail": {"code": "login_required", "message": "Please sign in."}})
    reset = current_tenant.set(tenant)
    try:
        return await call_next(request)
    finally:
        current_tenant.reset(reset)


def current_user() -> Tenant:
    t = current_tenant.get()
    if t is None:
        if base_settings().auth_enabled:
            raise HTTPException(401, {"code": "login_required", "message": "Please sign in."})
        return OWNER_FALLBACK
    return t


def require_owner() -> None:
    """Things that are shared by everyone on this computer (the scheduler, matching weights, automation caps, the .env file):
    only the owner may see or change them."""
    if not current_user().is_owner:
        raise HTTPException(403, {"code": "owner_only", "message": "Only the owner of this JobPilot can do this."})
