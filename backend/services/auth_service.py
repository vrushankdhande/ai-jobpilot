"""Accounts and sign-in.

* Accounts and sessions live in the owner's (base) database: ``accounts`` and ``account_sessions``. Each person's job-search data lives in
  their own database (config/tenant.py) - accounts never mix with it.
* Passwords: scrypt (stdlib) with a random salt per account; only the hash is stored, never the password. Compared in constant time.
* Sessions: a random 256-bit token in an HttpOnly, SameSite=Strict cookie; only its SHA-256 is stored, with an expiry. Logging out deletes it.
* The FIRST account is the owner and takes over the data that existed before logins. After that, only the owner can add people
  (no open sign-up). Repeated wrong passwords lock that e-mail out for a few minutes.
"""
import hashlib
import hmac
import re
import secrets
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymongo import ASCENDING
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from config.settings import PROJECT_ROOT, Settings
from config.tenant import Tenant, user_dir

ACCOUNTS, SESSIONS = "accounts", "account_sessions"
COOKIE = "jp_session"
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")
_N, _R, _P = 2 ** 14, 8, 1
MAX_FAILS, LOCK_SECONDS = 5, 300
PREFS_TEMPLATE = PROJECT_ROOT / "config" / "job_preferences.template.yaml"


class AuthError(Exception):
    """Wrong e-mail/password, expired session, not allowed. Messages are safe to show."""


class SetupDone(AuthError):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(got.hex(), digest)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_password_rules(password: str) -> None:
    if len(password) < 8:
        raise AuthError("The password must have at least 8 characters.")
    if len(password) > 200:
        raise AuthError("The password is too long.")


def _clean_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not _EMAIL.match(email):
        raise AuthError("Enter a valid e-mail address.")
    return email


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", (name or "").strip())[:80]
    if not name:
        raise AuthError("Enter a name.")
    return name


def ensure_auth_indexes(db: Database) -> None:
    db[ACCOUNTS].create_index([("email", ASCENDING)], unique=True, name="uq_account_email")
    db[ACCOUNTS].create_index([("user_id", ASCENDING)], unique=True, name="uq_account_user_id")
    db[SESSIONS].create_index([("token_hash", ASCENDING)], unique=True, name="uq_session_token")
    db[SESSIONS].create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_session_expiry")


class _Throttle:
    """Failed sign-ins per e-mail (in memory): after MAX_FAILS the e-mail is locked for LOCK_SECONDS."""

    def __init__(self, clock=time.monotonic):
        self.clock, self.fails, self.lock = clock, {}, threading.Lock()

    def check(self, key: str) -> None:
        with self.lock:
            n, since = self.fails.get(key, (0, 0.0))
            if n >= MAX_FAILS and self.clock() - since < LOCK_SECONDS:
                raise AuthError("Too many wrong passwords. Wait 5 minutes and try again.")
            if n >= MAX_FAILS:
                self.fails.pop(key, None)

    def failed(self, key: str) -> None:
        with self.lock:
            n, _ = self.fails.get(key, (0, 0.0))
            self.fails[key] = (n + 1, self.clock())

    def ok(self, key: str) -> None:
        with self.lock:
            self.fails.pop(key, None)


throttle = _Throttle()


def _public(acc: dict) -> dict:
    return {"user_id": acc["user_id"], "name": acc["name"], "email": acc["email"], "role": acc["role"], "created_at": acc.get("created_at"),
            "last_login_at": acc.get("last_login_at")}


class AuthService:
    def __init__(self, db: Database, settings: Settings, *, now_fn=None):
        self.db, self.settings = db, settings
        self.accounts, self.sessions = db[ACCOUNTS], db[SESSIONS]
        self.now = now_fn or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------ accounts
    def setup_needed(self) -> bool:
        return self.accounts.count_documents({}, limit=1) == 0

    def _new_account(self, name: str, email: str, password: str, role: str) -> dict:
        check_password_rules(password)
        acc = {"user_id": secrets.token_hex(16), "name": _clean_name(name), "email": _clean_email(email), "role": role,
               "password_hash": hash_password(password), "created_at": self.now(), "last_login_at": None}
        try:
            self.accounts.insert_one(dict(acc))
        except DuplicateKeyError:
            raise AuthError("An account with this e-mail already exists.") from None
        return acc

    def setup_owner(self, name: str, email: str, password: str) -> dict:
        """The first account: the owner. It keeps all the data that existed before logins were added."""
        if not self.setup_needed():
            raise SetupDone("The owner account already exists. Sign in instead.")
        ensure_auth_indexes(self.db)
        acc = self._new_account(name, email, password, "owner")
        if self.accounts.count_documents({"role": "owner"}) > 1:          # two first-time set-ups at the same moment: keep only the earliest
            first = self.accounts.find_one({"role": "owner"}, sort=[("created_at", 1), ("_id", 1)])
            if first["user_id"] != acc["user_id"]:
                self.accounts.delete_one({"user_id": acc["user_id"]})
                raise SetupDone("The owner account already exists. Sign in instead.")
        return _public(acc)

    def add_user(self, name: str, email: str, password: str) -> dict:
        """The owner adds a person. They get an empty, separate workspace (own database, own folders, default job preferences)."""
        acc = self._new_account(name, email, password, "user")
        self._prepare_workspace(acc["user_id"])
        return _public(acc)

    def _prepare_workspace(self, user_id: str) -> None:
        base = PROJECT_ROOT / user_dir(user_id)
        for sub in ("resumes", "generated", "screenshots", "browser_sessions", "notifications"):
            (base / sub).mkdir(parents=True, exist_ok=True)
        prefs = base / "job_preferences.yaml"
        if not prefs.exists():
            shutil.copyfile(PREFS_TEMPLATE, prefs)

    def list_users(self) -> list[dict]:
        return [_public(a) for a in self.accounts.find({}, {"_id": 0}).sort("created_at", 1)]

    def account(self, user_id: str) -> dict | None:
        return self.accounts.find_one({"user_id": user_id}, {"_id": 0})

    def change_password(self, user_id: str, current: str, new: str) -> None:
        acc = self.account(user_id)
        if acc is None or not verify_password(current, acc["password_hash"]):
            raise AuthError("The current password is wrong.")
        check_password_rules(new)
        self.accounts.update_one({"user_id": user_id}, {"$set": {"password_hash": hash_password(new)}})
        self.sessions.delete_many({"user_id": user_id})                # signed out everywhere; the caller signs in again

    def reset_password(self, user_id: str, new: str) -> None:
        """The owner sets a new password for another person (e.g. they forgot it)."""
        acc = self.account(user_id)
        if acc is None:
            raise AuthError("No such user.")
        check_password_rules(new)
        self.accounts.update_one({"user_id": user_id}, {"$set": {"password_hash": hash_password(new)}})
        self.sessions.delete_many({"user_id": user_id})

    def remove_user(self, user_id: str) -> None:
        """Removes the account and signs it out. Their data is kept (database and folders) so nothing is lost by a wrong click."""
        acc = self.account(user_id)
        if acc is None:
            raise AuthError("No such user.")
        if acc["role"] == "owner":
            raise AuthError("The owner account cannot be removed.")
        self.accounts.delete_one({"user_id": user_id})
        self.sessions.delete_many({"user_id": user_id})

    # ------------------------------------------------------------------ sessions
    def login(self, email: str, password: str) -> tuple[str, dict]:
        key = (email or "").strip().lower()[:256]
        throttle.check(key)
        acc = self.accounts.find_one({"email": key}, {"_id": 0}) if key else None
        if acc is None or not verify_password(password or "", acc["password_hash"]):
            if acc is None:
                verify_password(password or "", hash_password("timing-equaliser"))    # same work whether or not the e-mail exists
            throttle.failed(key)
            raise AuthError("Wrong e-mail or password.")
        throttle.ok(key)
        token = secrets.token_urlsafe(32)
        now = self.now()
        self.sessions.insert_one({"token_hash": _token_hash(token), "user_id": acc["user_id"], "created_at": now,
                                  "expires_at": now + timedelta(days=self.settings.session_days)})
        self.accounts.update_one({"user_id": acc["user_id"]}, {"$set": {"last_login_at": now}})
        return token, _public(acc)

    def logout(self, token: str | None) -> None:
        if token:
            self.sessions.delete_one({"token_hash": _token_hash(token)})

    def tenant_for(self, token: str | None) -> Tenant | None:
        if not token or len(token) > 200:
            return None
        s = self.sessions.find_one({"token_hash": _token_hash(token)}, {"_id": 0})
        if s is None:
            return None
        expires = s["expires_at"] if s["expires_at"].tzinfo else s["expires_at"].replace(tzinfo=timezone.utc)
        if expires <= self.now():
            self.sessions.delete_one({"token_hash": s["token_hash"]})
            return None
        acc = self.account(s["user_id"])
        if acc is None:
            return None
        return Tenant(user_id=acc["user_id"], name=acc["name"], role=acc["role"])


def workspace_path(user_id: str) -> Path:
    return PROJECT_ROOT / user_dir(user_id)
