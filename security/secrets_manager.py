"""Derives encryption keys from ``SECRET_KEY`` (config/settings.py). Nothing is stored; no key is ever logged or returned to an API."""
import base64
import hashlib

_SALT = b"ai-jobpilot/browser-session/v1"
MIN_SECRET_LENGTH = 16


class SecretUnavailable(Exception):
    """SECRET_KEY is missing or too short - encrypted features stay off (fail closed)."""


def derive_fernet_key(secret: str | None, purpose: str = "session") -> bytes:
    if not secret or len(secret) < MIN_SECRET_LENGTH:
        raise SecretUnavailable(f"SECRET_KEY must be set (at least {MIN_SECRET_LENGTH} characters) to store browser sessions.")
    raw = hashlib.pbkdf2_hmac("sha256", secret.encode(), _SALT + purpose.encode(), 200_000, dklen=32)
    return base64.urlsafe_b64encode(raw)
