"""One-time setup: lets JobPilot READ your job-alert e-mails (Naukri) from your own Gmail, read-only.

You sign in to Google yourself in your browser, so JobPilot never sees your Google password:

  1. Google Cloud Console -> APIs & Services -> enable the Gmail API, and create an OAuth client:
       * type "Desktop app"  (nothing else to set), or
       * type "Web application" with this Authorised redirect URI:  http://localhost:8765
     Download the client's JSON file.
  2. Run:   uv run python scripts/gmail_alerts_setup.py "C:\\path\\to\\client_secret_....json"
     Your browser opens Google's sign-in page; choose your Gmail and allow "Read your email" (read-only).
     The answer comes back to THIS computer only (http://localhost), is checked, and the result is saved.

Saved Fernet-encrypted with SECRET_KEY (storage/notifications/gmail_job_alerts.cred, git-ignored). No secret or token is ever printed.
Only Google is contacted: accounts.google.com (your sign-in, in your browser) and oauth2.googleapis.com (exchanging the one-time code).
Without the JSON file the script asks for the client id and secret instead (typing hidden).
Another person on this JobPilot connects THEIR Gmail with  --user their@email  (the e-mail they sign in to JobPilot with); it is saved in
their own folder (storage/users/<id>/notifications) and only their account reads it. Without --user it is the owner's Gmail.
To stop: delete that file, or remove JobPilot's access at https://myaccount.google.com/permissions.
"""
import base64
import getpass
import hashlib
import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import get_settings  # noqa: E402
from notifications.email_notifications import TOKEN_URL, CredentialStore  # noqa: E402
from security.secrets_manager import SecretUnavailable, derive_fernet_key  # noqa: E402

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
WEB_PORT = 8765                                   # a "Web application" client must list http://localhost:8765 as its redirect URI
WAIT_SECONDS = 300


def read_client(path: str | None) -> tuple[str, str, str]:
    """(client_id, client_secret, kind) from Google's downloaded JSON, or typed in. kind = 'installed' (Desktop app) or 'web'."""
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        kind = "installed" if "installed" in data else "web" if "web" in data else ""
        c = data.get(kind) or {}
        if not (kind and c.get("client_id") and c.get("client_secret")):
            raise ValueError("That is not a Google OAuth client file (expected an 'installed' or 'web' client with an id and secret).")
        return c["client_id"], c["client_secret"], kind
    cid = input("OAuth client id: ").strip()
    secret = getpass.getpass("OAuth client secret (hidden): ").strip()
    kind = "web" if input("Client type - Desktop app or Web application? [d/w]: ").strip().lower().startswith("w") else "installed"
    return cid, secret, kind


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def wait_for_code(server: HTTPServer, state: str) -> dict:
    """Serves the single redirect from Google on localhost and returns its query parameters."""
    result: dict = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            if "code" not in q and "error" not in q:
                self.send_response(404)
                self.end_headers()
                return
            ok = q.get("state") == state and "code" in q
            result.update(q if ok else {"error": q.get("error") or "state_mismatch"})
            body = ("JobPilot: Gmail is connected. You can close this tab." if ok else
                    "JobPilot: the sign-in was not completed. Close this tab and run the setup again.").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, *args):  # keep the console clean (the URL holds the one-time code)
            pass

    server.RequestHandlerClass = Handler
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    done.wait(WAIT_SECONDS)
    server.shutdown()
    return result


def _use_account(email: str) -> str | None:
    """Run as that JobPilot person: their own credentials file. Returns an error message, or None."""
    from backend.services.auth_service import ACCOUNTS
    from config.tenant import Tenant, current_tenant
    from pymongo import MongoClient
    base = get_settings()
    client = MongoClient(base.mongodb_uri, serverSelectionTimeoutMS=3000)
    try:
        acc = client[base.mongodb_database][ACCOUNTS].find_one({"email": email.strip().lower()}, {"_id": 0, "user_id": 1, "name": 1, "role": 1})
    finally:
        client.close()
    if acc is None:
        return f"No JobPilot account with the e-mail {email}. The owner adds people under 'My account & users'."
    current_tenant.set(Tenant(user_id=acc["user_id"], name=acc["name"], role=acc["role"]))
    print(f"Connecting the Gmail of: {acc['name']}")
    return None


def main(argv: list[str]) -> int:
    argv = list(argv)
    if "--user" in argv:
        i = argv.index("--user")
        if i + 1 >= len(argv):
            print("Usage: --user their@email")
            return 1
        problem = _use_account(argv[i + 1])
        del argv[i:i + 2]
        if problem:
            print(problem)
            return 1
    s = get_settings()
    try:
        derive_fernet_key(s.secret_key, "gmail")
    except SecretUnavailable as exc:
        print(f"Cannot continue: {exc}")
        return 1
    try:
        cid, secret, kind = read_client(argv[1] if len(argv) > 1 else None)
    except (OSError, ValueError) as exc:
        print(f"Cannot read the client: {exc}")
        return 1

    port = WEB_PORT if kind == "web" else 0
    try:
        server = HTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    except OSError:
        print(f"Port {port} is in use. Close the program using it and run the setup again.")
        return 1
    redirect = f"http://localhost:{server.server_address[1]}" if kind == "web" else f"http://127.0.0.1:{server.server_address[1]}"
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(24)
    url = AUTH_URL + "?" + urlencode({"client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE, "access_type": "offline",
                                       "prompt": "consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Opening Google sign-in in your browser. Choose the Gmail that receives your job alerts and allow read-only access.")
    if kind == "web":
        print(f"(This client must list  {redirect}  under 'Authorised redirect URIs' in Google Cloud Console.)")
    print("If no browser opens, copy this address into your browser:\n" + url)
    webbrowser.open(url)
    got = wait_for_code(server, state)
    if "code" not in got:
        err = got.get("error") or "no answer within 5 minutes"
        print(f"Not connected: {err}.")
        if err == "redirect_uri_mismatch":
            print(f"Add  {redirect}  to the client's Authorised redirect URIs in Google Cloud Console and run the setup again.")
        return 1

    r = httpx.post(TOKEN_URL, data={"code": got["code"], "client_id": cid, "client_secret": secret, "redirect_uri": redirect,
                                    "grant_type": "authorization_code", "code_verifier": verifier}, timeout=30)
    tok = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or not tok.get("refresh_token"):
        print(f"Google did not give a long-lived permission (HTTP {r.status_code}: {tok.get('error', 'unknown')}). "
              "Remove JobPilot at https://myaccount.google.com/permissions and run the setup again.")
        return 1
    if SCOPE not in (tok.get("scope") or "").split():
        print("Read-only Gmail access was not granted (the box was unticked?). Run the setup again and allow it.")
        return 1
    CredentialStore(s.job_alert_credentials_path, s.secret_key).save(cid, secret, tok["refresh_token"])
    print(f"Saved (encrypted) to {s.job_alert_credentials_file}. Job-alert and rejection e-mails are read on the next check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
