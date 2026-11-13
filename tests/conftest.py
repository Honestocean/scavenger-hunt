"""Fixtures shared by every test file: one local server and one browser for the
whole run, and a fresh phone-sized app context per test."""
import functools
import http.server
import threading

import pytest
from playwright.sync_api import sync_playwright

from test_ui import CHROME, HOME, LEAFLET_DIST, REPO, App


@pytest.fixture(scope="session")
def base_url():
    assert (LEAFLET_DIST / "leaflet.js").is_file(), (
        f"Leaflet not found in {LEAFLET_DIST}; see tests/README.md")
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

    handler = functools.partial(Quiet, directory=str(REPO))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://localhost:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True)
        yield b
        b.close()


@pytest.fixture
def make_app(browser, base_url):
    apps = []

    def factory(start=HOME, accuracy=5):
        a = App(browser, base_url, start, accuracy)
        apps.append(a)
        return a

    yield factory
    errors = [e for a in apps for e in a.errors]
    for a in apps:
        a.close()
    assert not errors, f"uncaught page errors: {errors}"


@pytest.fixture
def app(make_app):
    return make_app()
