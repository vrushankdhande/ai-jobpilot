"""Store Gmail OAuth credentials ENCRYPTED (needs SECRET_KEY in .env). Run:  uv run python scripts/gmail_setup.py

Get a refresh token yourself (for example with Google's OAuth Playground using your own OAuth client and the scope
https://www.googleapis.com/auth/gmail.send). Nothing is echoed back; no Gmail password is ever used.
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import get_settings  # noqa: E402
from notifications.email_notifications import CredentialStore  # noqa: E402
from security.secrets_manager import SecretUnavailable, derive_fernet_key  # noqa: E402


def main() -> int:
    s = get_settings()
    try:
        derive_fernet_key(s.secret_key, "gmail")
    except SecretUnavailable as exc:
        print(f"Cannot continue: {exc}")
        return 1
    cid = input("OAuth client id: ").strip()
    secret = getpass.getpass("OAuth client secret (hidden): ").strip()
    token = getpass.getpass("Refresh token (hidden): ").strip()
    CredentialStore(s.notification_credentials_path, s.secret_key).save(cid, secret, token)
    print(f"Saved (encrypted) to {s.notification_credentials_file}. Set NOTIFICATIONS_ENABLED=true and NOTIFICATION_RECIPIENT in .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
