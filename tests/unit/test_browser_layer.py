"""Browser manager, screenshots, locator engine and manual-login session flow against LOCAL pages in a real Chromium-family browser."""
import pytest

from browser.browser_manager import BrowserError, BrowserManager
from browser.locator_engine import LocatorEngine
from browser.screenshot_manager import ScreenshotManager
from browser.session_manager import SessionManager
from config.settings import get_settings

pytestmark = pytest.mark.browser


def settings(**kw):
    return get_settings().model_copy(update={"allow_local_test_forms": True, "browser_timeout_ms": 1500, **kw})


def test_browser_starts_works_and_cleans_up_safely(browser_ok, form_server):
    bm = BrowserManager(settings(), allowed_hosts=["127.0.0.1"])
    assert bm.start() is bm and bm.start() is bm and bm.channel_used                                   # idempotent start; reports which browser
    with bm.session() as page:
        assert bm.goto(page, form_server.url("/apply")) == 200
        assert "Application" in page.title() or page.locator("h1").inner_text()
    assert bm._contexts == []                                                                          # session context closed
    bm.close()
    bm.close()                                                                                         # safe to repeat
    with pytest.raises(BrowserError):
        bm.new_context()
    with pytest.raises(RuntimeError):                                                                  # cleanup also runs when the body fails
        with BrowserManager(settings()) as bm2:
            raise RuntimeError("boom")
    assert bm2._browser is None and bm2._pw is None


def test_headless_and_headed_are_configurable_and_the_missing_browser_is_reported(browser_ok):
    assert BrowserManager(settings(browser_headless=True)).headless is True and BrowserManager(settings(browser_headless=False)).headless is False
    assert BrowserManager(settings(browser_headless=False), headless=True).headless is True
    from browser.browser_manager import BrowserUnavailable
    with pytest.raises(BrowserUnavailable) as exc:
        BrowserManager(settings(browser_channel="no-such-browser-channel")).start()
    assert "browser" in str(exc.value).lower() and BrowserManager(settings())._browser is None


def test_navigation_is_restricted_to_allowed_hosts(browser_ok, form_server):
    with BrowserManager(settings(), allowed_hosts=["127.0.0.1"]) as bm:
        with bm.session() as page:
            for bad in ("https://example.com/", "http://10.0.0.5/", "http://localhost:%d/apply" % form_server.port, "file:///C:/Windows/win.ini", "javascript:1"):
                with pytest.raises(BrowserError):
                    bm.goto(page, bad)
    with BrowserManager(settings(allow_local_test_forms=False), allowed_hosts=["127.0.0.1"]) as bm:
        with bm.session() as page:
            with pytest.raises(BrowserError):
                bm.goto(page, form_server.url("/apply"))                                                # loopback refused unless explicitly enabled


def test_timeouts_are_reported_as_transient_without_hanging(browser_ok, form_server):
    with BrowserManager(settings(browser_timeout_ms=1000), allowed_hosts=["127.0.0.1"]) as bm:
        with bm.session() as page:
            with pytest.raises(BrowserError) as exc:
                bm.goto(page, form_server.url("/slow"))                                                 # server sleeps 3s; timeout is 2s
    assert exc.value.transient is True and "too long" in str(exc.value)


def test_screenshots_are_stored_privately_and_cannot_be_traversed(browser_ok, form_server, tmp_path):
    shots = ScreenshotManager(tmp_path / "shots")
    with BrowserManager(settings(), allowed_hosts=["127.0.0.1"]) as bm, bm.session() as page:
        bm.goto(page, form_server.url("/apply"))
        sid = shots.capture(page, "a" * 32)
    assert sid and sid.startswith("a" * 12) and shots.read(sid)[:4] == b"\x89PNG" and (tmp_path / "shots" / sid).is_file()
    sid2 = shots.save(b"\x89PNG-second", "a" * 32)
    assert sid2 != sid and shots.read(sid)[:4] == b"\x89PNG"                                            # never overwritten
    for bad in ("../x.png", sid.replace(".png", ""), "..\\..\\secret.png", "", None, "a" * 12 + "_" + "g" * 16 + ".png"):
        assert shots.read(bad) is None
    assert shots.delete([sid, "../evil.png"]) == 1 and shots.read(sid) is None
    assert shots.capture(object(), "a" * 32) is None                                                    # a failed capture never raises


def test_locator_reads_real_forms_by_accessible_labels(browser_ok, form_server):
    with BrowserManager(settings(), allowed_hosts=["127.0.0.1"]) as bm, bm.session() as page:
        bm.goto(page, form_server.url("/apply"))
        snap = LocatorEngine(page).read()
        by = {f.label: f for f in snap.fields}
        assert snap.automatable and [s.text for s in snap.submit] == ["Submit application"] and snap.fingerprint
        assert by["Full name"].required and by["Email address"].kind == "email" and by["Mobile number"].kind == "tel" and not by["Mobile number"].required
        assert by["Resume/CV"].kind == "file" and by["Resume/CV"].accept == ".pdf,.docx" and by["Cover letter"].kind == "file"
        assert by["Are you willing to relocate?"].kind == "select" and by["Are you willing to relocate?"].options == ["Select", "Yes", "No"]
        radio = by["Do you have the right to work in India?"]                                         # label comes from the fieldset <legend>
        assert radio.kind == "radio" and radio.options == ["Yes", "No"] and len(radio.option_marks) == 2 and radio.required
        assert by["I agree to the privacy policy"].kind == "checkbox" and by["I agree to the privacy policy"].required
        assert by["Tell us about yourself"].kind == "textarea"
        again = LocatorEngine(page).read()
        assert again.fingerprint == snap.fingerprint                                                  # stable between reads
        assert LocatorEngine(page).locator(by["Full name"].id).count() == 1


def test_locator_flags_protections_and_unusable_forms(browser_ok, form_server):
    expect = {"/captcha": "CAPTCHA", "/multistep": "multi-step", "/unlabelled": "identified", "/twosubmits": "More than one submit"}
    with BrowserManager(settings(), allowed_hosts=["127.0.0.1"]) as bm:
        for path, needle in expect.items():
            with bm.session() as page:
                bm.goto(page, form_server.url(path))
                snap = LocatorEngine(page).read()
                assert not snap.automatable and any(needle in i for i in snap.issues), (path, snap.issues)
        with bm.session() as page:
            status = bm.goto(page, form_server.url("/login"))
            snap = LocatorEngine(page).read()
            assert not snap.found and snap.has_login_wall and SessionManager(settings()).assess(page, status).login_required
        with bm.session() as page:
            status = bm.goto(page, form_server.url("/forbidden"))
            assert SessionManager(settings()).assess(page, status).blocked_status == 403
        with bm.session() as page:
            bm.goto(page, form_server.url("/captcha"))
            v = SessionManager(settings()).assess(page, 200)
            assert v.captcha and v.must_stop


def test_manual_login_lets_the_human_log_in_and_stores_only_an_encrypted_session(browser_ok, form_server, tmp_path):
    s = settings(persist_browser_sessions=True, secret_key="s" * 32, browser_sessions_dir=str(tmp_path / "sessions"))
    sm = SessionManager(s)
    typed = []

    def human(page, ctx):                      # stands in for the person: sets a cookie as a login would, then leaves the login page
        if not typed:
            typed.append(1)
            ctx.add_cookies([{"name": "sid", "value": "HUMANSESSION999", "url": form_server.url("/")}])
            page.goto(form_server.url("/apply"))
    with BrowserManager(s, allowed_hosts=["127.0.0.1"]) as bm:
        assert sm.manual_login(bm, form_server.url("/login"), "127.0.0.1", timeout_s=20, poll_s=0.1, while_waiting=human) is True
        raw = next((tmp_path / "sessions").glob("*.session")).read_bytes()
        assert b"HUMANSESSION999" not in raw                                                          # encrypted at rest
        state = sm.load("127.0.0.1")
        assert any(c["name"] == "sid" and c["value"] == "HUMANSESSION999" for c in state["cookies"])
        with bm.session(state) as page:                                                               # the saved session is reusable (still the human's login)
            bm.goto(page, form_server.url("/apply"))
            assert any(c["name"] == "sid" for c in page.context.cookies())
    assert sm.forget("127.0.0.1") and sm.load("127.0.0.1") is None
