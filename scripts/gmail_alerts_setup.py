"""One-time setup: lets JobPilot READ your job-alert e-mails (Naukri) from your own Gmail, read-only.

You create the authorisation yourself, so JobPilot never sees your Google password:
  1. Google Cloud Console -> create (or reuse) an OAuth client of type "Desktop app"; enable the Gmail API.
  2. Google OAuth Playground (https://developers.google.com/oauthplayground): gear icon -> "Use your own OAuth credentials" ->
     enter that client id/secret; in step 1 enter the scope  https://www.googleapis.com/auth/gmail.readonly  -> Authorize ->
     sign in -> step 2 "Exchange authorization code for tokens" -> copy the refresh token.
  3. Run:  uv run python scripts/gmail_alerts_setup.py   and paste the three values (hidden input).
They are stored Fernet-encrypted with SECRET_KEY (storage/notifications/gmail_job_alerts.cred, git-ignored). Nothing is echoed back.
To stop: delete that file, or revoke JobPilot's access at https://myaccount.google.com/permissions.
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
    token = getpass.getpass("Refresh token for scope gmail.readonly (hidden): ").strip()
    CredentialStore(s.job_alert_credentials_path, s.secret_key).save(cid, secret, token)
    print(f"Saved (encrypted) to {s.job_alert_credentials_file}. Restart the backend; job-alert e-mails are read on the next discovery run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
