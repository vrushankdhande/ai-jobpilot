"""Shared FastAPI security dependencies and HTTP hardening middleware."""
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from config.settings import Settings, get_settings

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
