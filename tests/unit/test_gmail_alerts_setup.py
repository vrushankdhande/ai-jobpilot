"""The Gmail job-alert setup script: reads Google's client file, signs in through the browser with PKCE, and accepts the answer only on
localhost with the right state. Google itself is never contacted here (no_network)."""
import base64
import hashlib
import importlib.util
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("no_network")
spec = importlib.util.spec_from_file_location("gmail_alerts_setup", Path(__file__).resolve().parents[2] / "scripts" / "gmail_alerts_setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_reads_desktop_and_web_client_files_and_rejects_anything_else(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"installed": {"client_id": "id1", "client_secret": "s1"}}))
    assert setup.read_client(str(f)) == ("id1", "s1", "installed")
    f.write_text(json.dumps({"web": {"client_id": "id2", "client_secret": "s2"}}))
    assert setup.read_client(str(f)) == ("id2", "s2", "web")
    f.write_text(json.dumps({"service_account": {}}))
    with pytest.raises(ValueError):
        setup.read_client(str(f))


def test_pkce_challenge_is_the_sha256_of_the_verifier():
    v, c = setup.pkce()
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=") and len(v) >= 43


def _hit(port, query):
    return urllib.request.urlopen(f"http://127.0.0.1:{port}/?{query}", timeout=5).read().decode()


@pytest.mark.parametrize("query,expected", [("code=abc&state=S1", {"code": "abc", "state": "S1"}), ("code=abc&state=WRONG", {"error": "state_mismatch"}),
                                            ("error=access_denied&state=S1", {"error": "access_denied"})])
def test_only_googles_redirect_with_the_right_state_is_accepted(query, expected):
    server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = server.server_address[1]
    out = {}
    t = threading.Thread(target=lambda: out.update(setup.wait_for_code(server, "S1")))
    t.start()
    for _ in range(50):                                     # the server thread starts serving inside wait_for_code
        try:
            body = _hit(port, query)
            break
        except OSError:
            threading.Event().wait(0.05)
    t.join(10)
    assert out == expected
    assert ("connected" in body) == ("code" in expected)
