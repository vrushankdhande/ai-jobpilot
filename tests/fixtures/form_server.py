"""Controlled LOCAL application forms for Phase 6 tests (http://127.0.0.1:<port>). No third-party site is ever contacted.

Each path is one scenario. The server records every POST (field values, uploaded file name + sha256) so tests can assert exactly what a
form received - and how many times (duplicate prevention).
"""
import hashlib
import html
import json
import threading
from email.parser import BytesParser
from email import policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CSS = "<style>body{font-family:sans-serif;max-width:640px;margin:20px auto}label{display:block;margin-top:10px}.error{color:#b00}</style>"


def page(body: str, title: str = "Apply") -> bytes:
    return f"<!doctype html><html><head><meta charset=utf-8><title>{title}</title>{CSS}</head><body>{body}</body></html>".encode()


def application_form(extra_top: str = "", errors: list[str] | None = None, *, unlabelled: bool = False, multistep: bool = False,
                     dynamic: bool = False, cover_required: bool = False, submit_text: str = "Submit application", two_submits: bool = False) -> bytes:
    err = "".join(f'<div role="alert" class="error">{html.escape(e)}</div>' for e in (errors or []))
    name_field = ('<input name="mystery" type="text">' if unlabelled else
                  '<label for="full_name">Full name *</label><input id="full_name" name="full_name" type="text" required>')
    dyn = ('<script>document.getElementById("full_name").addEventListener("input",()=>{if(!document.getElementById("extra")){'
           'const l=document.createElement("label");l.textContent="Extra question";const i=document.createElement("input");i.id="extra";i.name="extra";'
           'l.appendChild(i);document.getElementById("end").before(l);}});</script>') if dynamic else ""
    nav = '<button type="button">Next</button>' if multistep else (f'<button type="submit">{submit_text}</button>' + ('<button type="submit">Apply now</button>' if two_submits else ""))
    cover_req = " required" if cover_required else ""
    return page(f"""<h1>Senior Machine Learning Engineer - Application</h1>{extra_top}{err}
<form method="post" enctype="multipart/form-data" action="">
 {name_field}
 <label for="email">Email address *</label><input id="email" name="email" type="email" required>
 <label for="phone">Mobile number</label><input id="phone" name="phone" type="tel">
 <label for="city">City</label><input id="city" name="city" type="text">
 <label for="li">LinkedIn Profile</label><input id="li" name="linkedin" type="url">
 <label for="emp">Current employer</label><input id="emp" name="employer" type="text">
 <label for="yrs">Years of experience</label><input id="yrs" name="years" type="text">
 <label for="resume">Resume/CV *</label><input id="resume" name="resume" type="file" accept=".pdf,.docx" required>
 <label for="cl">Cover letter{' *' if cover_required else ''}</label><input id="cl" name="cover_letter" type="file" accept=".pdf,.docx"{cover_req}>
 <label for="about">Tell us about yourself *</label><textarea id="about" name="about" required></textarea>
 <label for="reloc">Are you willing to relocate? *</label>
 <select id="reloc" name="relocate" required><option value="">Select</option><option>Yes</option><option>No</option></select>
 <fieldset><legend>Do you have the right to work in India? *</legend>
  <label><input type="radio" name="authorised" value="yes" required> Yes</label><label><input type="radio" name="authorised" value="no"> No</label></fieldset>
 <label for="sal">Expected salary</label><input id="sal" name="salary" type="text">
 <label for="gender">Gender</label><select id="gender" name="gender"><option value="">Prefer not to say</option><option>Female</option><option>Male</option></select>
 <label><input type="checkbox" name="consent" value="1" required> I agree to the privacy policy *</label>
 <div id="end"></div>{nav}
</form>{dyn}""")


class _State:
    posts: list[dict] = []
    lock = threading.Lock()


def _parse_multipart(content_type: str, body: bytes) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    out: dict = {"fields": {}, "files": {}}
    for part in msg.iter_parts():
        name, fn = part.get_param("name", header="content-disposition"), part.get_filename()
        data = part.get_payload(decode=True) or b""
        if fn:
            if data:
                out["files"][name] = {"filename": fn, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        else:
            out["fields"][name] = data.decode("utf-8", "replace")
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):  # silence
        pass

    def _send(self, body: bytes, status: int = 200, ctype: str = "text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)
        if path.startswith("/confirmed"):
            ref = qs.get("ref", [""])[0]
            msg = f"<h1>Thank you for applying!</h1><p>Your application has been submitted.</p>" + (f"<p>Application reference: {html.escape(ref)}</p>" if ref else "")
            return self._send(page(msg, "Application submitted"))
        if path == "/slow":
            import time
            time.sleep(3)
            return self._send(page("<h1>slow</h1>"))
        if path == "/redirect-off":   # a page trying to send the automation to another (non-allowed) host
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.2:{self.server.server_address[1]}/apply")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/forbidden":
            return self._send(page("<h1>Access denied</h1>"), 403)
        if path == "/login":
            return self._send(page('<h1>Sign in to continue</h1><form method="post"><label>Email<input name="u"></label><label>Password<input type="password" name="p"></label><button>Sign in</button></form>', "Sign in"))
        if path == "/captcha":
            return self._send(application_form('<div class="g-recaptcha" data-sitekey="test"></div>'))
        if path == "/multistep":
            return self._send(application_form(multistep=True))
        if path == "/unlabelled":
            return self._send(application_form(unlabelled=True))
        if path == "/dynamic":
            return self._send(application_form(dynamic=True))
        if path == "/coverreq":
            return self._send(application_form(cover_required=True))
        if path == "/twosubmits":
            return self._send(application_form(two_submits=True))
        if path in ("/apply", "/reject", "/ambiguous", "/ref", "/noref", "/servererror", "/echo", "/nomsg-gone", "/formagain", "/timeout"):
            return self._send(application_form())
        if path == "/posts":
            with _State.lock:
                return self._send(json.dumps(_State.posts).encode(), ctype="application/json")
        self._send(page("<h1>Not found</h1>"), 404)

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        ctype = self.headers.get("Content-Type", "")
        data = _parse_multipart(ctype, body) if ctype.startswith("multipart/") else {"fields": {}, "files": {}}
        with _State.lock:
            _State.posts.append({"path": path, **data})
        f = data["fields"]
        missing = [k for k in ("full_name", "email", "about") if not f.get(k)] + ([] if data["files"].get("resume") else ["resume"])
        if path == "/reject":   # the site always refuses (e.g. server-side rule)
            return self._send(application_form(errors=["Please check your answers and try again."]))
        if missing:
            return self._send(application_form(errors=[f"{m} is required" for m in missing]))
        if path == "/timeout":   # the application IS accepted (it is recorded above) but the response arrives far too late: the outcome is unknown to the client
            import time
            time.sleep(6)
            return self._send(page("<h1>Thank you for applying!</h1><p>Your application has been submitted.</p>", "Submitted"))
        if path == "/ref":
            return self._send(page("<h1>Thank you for applying!</h1><p>Your application has been submitted.</p><p>Application reference: APP-2026-0042</p>", "Submitted"))
        if path == "/noref":
            return self._send(page("<h1>Application submitted</h1><p>Thanks, we have received it.</p>", "Submitted"))
        if path == "/ambiguous":
            return self._send(page("<h1>Processing</h1><p>Please wait while we process your request.</p>", "Processing"))
        if path == "/servererror":
            return self._send(page("<h1>Something went wrong</h1>"), 500)
        if path == "/formagain":
            return self._send(application_form())
        if path == "/nomsg-gone":
            return self._send(page("<h1>Welcome back</h1>"))
        self.send_response(302)
        self.send_header("Location", "/confirmed?ref=")
        self.send_header("Content-Length", "0")
        self.end_headers()


class FormServer:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.alias = None            # a second listener on 127.0.0.2 (same port): a different "site" for redirect tests

    def start(self) -> "FormServer":
        self.thread.start()
        try:
            self.alias = ThreadingHTTPServer(("127.0.0.2", self.port), Handler)
            threading.Thread(target=self.alias.serve_forever, daemon=True).start()
        except OSError:
            self.alias = None
        return self

    def stop(self) -> None:
        for s in (self.httpd, self.alias):
            if s is not None:
                s.shutdown()
                s.server_close()

    def url(self, path: str = "/apply") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    @property
    def posts(self) -> list[dict]:
        with _State.lock:
            return list(_State.posts)

    def reset(self) -> None:
        with _State.lock:
            _State.posts.clear()
