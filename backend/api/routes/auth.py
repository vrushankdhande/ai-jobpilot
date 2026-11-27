"""Sign-in API (/api/auth). The session lives in an HttpOnly cookie; the browser's JavaScript never sees it."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, Field

from backend.api.security import current_user, require_owner
from backend.services.auth_service import COOKIE, AuthError, AuthService, SetupDone
from config.settings import base_settings
from config.tenant import Tenant
from database.connection import DatabaseManager, get_database_manager

router = APIRouter(prefix="/api/auth", tags=["auth"])
UserId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]


def get_auth_service(manager: DatabaseManager = Depends(get_database_manager)) -> AuthService:
    return AuthService(manager.base_database(), base_settings())


class SetupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=256)
    password: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=200)


class PasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class ResetRequest(BaseModel):
    new_password: str = Field(min_length=1, max_length=200)


def _err(fn, status: int = 400):
    try:
        return fn()
    except SetupDone as exc:
        raise HTTPException(409, str(exc)) from None
    except AuthError as exc:
        raise HTTPException(status, str(exc)) from None


def _set_cookie(resp: Response, token: str) -> None:
    s = base_settings()
    resp.set_cookie(COOKIE, token, max_age=s.session_days * 86400, httponly=True, samesite="strict", secure=s.cookie_secure, path="/")


@router.get("/status", summary="Is anyone signed in? Is the first-time set-up still needed?")
def status(request: Request, svc: AuthService = Depends(get_auth_service)):
    if not base_settings().auth_enabled:
        return {"auth_enabled": False, "setup_needed": False, "user": None}
    t = svc.tenant_for(request.cookies.get(COOKIE))
    return {"auth_enabled": True, "setup_needed": svc.setup_needed(),
            "user": None if t is None else {"user_id": t.user_id, "name": t.name, "role": t.role}}


@router.post("/setup", status_code=201, summary="First start only: create the owner account (it keeps the existing data) and sign in")
def setup(body: SetupRequest, response: Response, svc: AuthService = Depends(get_auth_service)):
    _err(lambda: svc.setup_owner(body.name, body.email, body.password))
    token, user = _err(lambda: svc.login(body.email, body.password), 401)
    _set_cookie(response, token)
    return {"user": user}


@router.post("/login", summary="Sign in")
def login(body: LoginRequest, response: Response, svc: AuthService = Depends(get_auth_service)):
    token, user = _err(lambda: svc.login(body.email, body.password), 401)
    _set_cookie(response, token)
    return {"user": user}


@router.post("/logout", summary="Sign out")
def logout(request: Request, response: Response, svc: AuthService = Depends(get_auth_service)):
    svc.logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/password", summary="Change your own password (signs you out everywhere)")
def change_password(body: PasswordRequest, response: Response, me: Tenant = Depends(current_user), svc: AuthService = Depends(get_auth_service)):
    _err(lambda: svc.change_password(me.user_id, body.current_password, body.new_password))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/users", dependencies=[Depends(require_owner)], summary="Owner: everyone who can sign in")
def users(svc: AuthService = Depends(get_auth_service)):
    return {"items": svc.list_users()}


@router.post("/users", status_code=201, dependencies=[Depends(require_owner)], summary="Owner: add a person (they get their own, empty workspace)")
def add_user(body: SetupRequest, svc: AuthService = Depends(get_auth_service)):
    return _err(lambda: svc.add_user(body.name, body.email, body.password))


@router.post("/users/{user_id}/password", dependencies=[Depends(require_owner)], summary="Owner: set a new password for a person")
def reset_password(user_id: UserId, body: ResetRequest, svc: AuthService = Depends(get_auth_service)):
    _err(lambda: svc.reset_password(user_id, body.new_password))
    return {"ok": True}


@router.delete("/users/{user_id}", dependencies=[Depends(require_owner)], summary="Owner: remove a person's sign-in (their data is kept)")
def remove_user(user_id: UserId, svc: AuthService = Depends(get_auth_service)):
    _err(lambda: svc.remove_user(user_id))
    return {"ok": True}
