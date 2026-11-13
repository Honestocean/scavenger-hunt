"""Offline tests: vendored Leaflet, the service worker (sw.js) and the
"find signal and reload" message.

These tests serve a temporary COPY of the site (index.html, sw.js, vendor/)
under a sub-path, like GitHub Pages does, from a server the test can stall or
cut off. Service workers are allowed only in the contexts made here; every
other test file runs with them blocked (see App.__init__ in test_ui.py).
"""
import functools
import http.server
import os
import shutil
import threading
import time

import pytest
from playwright.sync_api import expect

from test_puzzles import C1, C2, W, clue, go, only_overlay, type_word
from test_ui import BLANK_PNG, HOME, START, App, b64u, find_clue, hunt_obj, north, unlock

SUBPATH = "scavenger-hunt"  # the site is served from /scavenger-hunt/, as on GitHub Pages
PASSWORD = "sea breeze"
# The app's script has run and the first GPS fix has arrived. (Not "typeof map":
# the #map element makes window.map exist before the script does.)
BOOTED = "window.__fixCount >= 1 && typeof myPos !== 'undefined' && myPos !== null"
HUNT = hunt_obj([clue(C1, W), dict(C2)], title="Offline hunt")


class Site:
    """A temp copy of the site on its own local server, with a network switch."""

    def __init__(self, root):
        self.root = root
        self.dir = root / SUBPATH
        self.dir.mkdir()
        repo = App_repo()
        for name in ("index.html", "sw.js"):
            if (repo / name).is_file():
                shutil.copy2(repo / name, self.dir / name)
        if (repo / "vendor").is_dir():
            shutil.copytree(repo / "vendor", self.dir / "vendor")
        self.mode = "up"  # "up" | "down" (connection dropped) | "stall" (no answer) | "cut" (body cut short)
        self.release = threading.Event()
        self.hits = []
        site = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a, **k):
                pass

            def do_GET(self):
                site.hits.append((site.mode, self.path))
                if site.mode == "stall":
                    site.release.wait(30)
                if site.mode == "cut":
                    # Good headers, then the connection dies half-way through the body.
                    name = self.path.split("?")[0].split("#")[0]
                    f = site.root / (name.lstrip("/") + ("index.html" if name.endswith("/") else ""))
                    body = f.read_bytes() if f.is_file() else b"x" * 2000
                    self.send_response(200)
                    self.send_header("Content-Type", self.guess_type(str(f)))
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body[:len(body) // 2])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                if site.mode != "up":
                    self.close_connection = True
                    return  # no response at all: the fetch fails
                super().do_GET()

        self.srv = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://localhost:{self.srv.server_address[1]}/{SUBPATH}"

    def set_index(self, old, new):
        f = self.dir / "index.html"
        text = f.read_text(encoding="utf-8")
        assert old in text
        f.write_text(text.replace(old, new, 1), encoding="utf-8")
        later = time.time() + 5  # a clearly newer Last-Modified
        os.utime(f, (later, later))

    def close(self):
        self.mode = "down"
        self.release.set()
        self.srv.shutdown()
        self.srv.server_close()


def App_repo():
    from test_ui import REPO
    return REPO


@pytest.fixture
def site(tmp_path):
    s = Site(tmp_path)
    yield s
    s.close()


@pytest.fixture
def make_sw_app(browser, site):
    """Apps whose context ALLOWS service workers, on the temp site."""
    apps = []

    def factory(start=START, service_workers="allow"):
        a = App(browser, site.base, start, 5, service_workers=service_workers)
        a.requests = []
        a.responses = []
        a.page.on("request", lambda r: a.requests.append(r.url))
        a.page.on("response", lambda r: a.responses.append(r))
        apps.append(a)
        return a

    yield factory
    errors = [e for a in apps for e in a.errors if not getattr(a, "expect_errors", False)]
    for a in apps:
        a.close()
    assert not errors, f"uncaught page errors: {errors}"


def sw_ready(app):
    """Wait until the service worker is installed (precache done) and activated.

    It then serves the NEXT page load. Whether it also took over the page that
    is already open (clients.claim) depends on timing when that page was itself
    mid-reload, so only the registration test waits for the controller."""
    app.page.wait_for_function("'serviceWorker' in navigator")
    app.page.evaluate(
        """navigator.serviceWorker.ready.then(r => { const w = r.active;
             const done = () => { if (w.state === 'activated') window.__swReady = true; };
             w.addEventListener('statechange', done); done(); }); 0""")
    app.page.wait_for_function("window.__swReady === true", timeout=20000)


def go_offline(app, site, mode="down"):
    site.mode = mode
    if mode == "down":
        app.ctx.set_offline(True)


def reload(app, timeout=25000):
    app.page.reload(timeout=timeout, wait_until="commit")
    app.page.wait_for_function(BOOTED, timeout=timeout)


def encrypted_link(app):
    """A password-protected player link, made by the app's own crypto."""
    payload = app.page.evaluate("([o, pw]) => encryptHunt(o, pw)", [HUNT, PASSWORD])
    assert payload.startswith("e.")
    return "#hunt=" + payload


def solve_and_arrive(app):
    """Wordle on clue 1, then a three-fix arrival that shows the next stage."""
    p = app.page
    only_overlay(app, "#puzzle")
    expect(p.locator("#wordle-grid")).to_be_visible()
    type_word(app, "maple")
    only_overlay(app, "#puzzle")
    type_word(app, "apple")
    only_overlay(app, "#unlocked")
    expect(p.locator("#unlocked-clue")).to_have_text(C1["t"])
    go(app)
    expect(p.locator("#play-clue")).to_have_text(C1["t"])
    app.fixes(2, C1["lat"], C1["lng"])
    expect(p.locator("#found")).to_be_hidden()
    find_clue(app, C1["lat"], C1["lng"], 1)
    only_overlay(app, "#found")
    expect(p.locator("#found-clue")).to_have_text(C2["t"])


# ============================================================ vendored Leaflet
def test_leaflet_is_vendored_with_relative_urls_and_no_cdn(make_sw_app, site):
    a = make_sw_app(service_workers="block").open("")
    p = a.page
    a.settle(500)
    assert p.locator('script[src]').evaluate_all("els => els.map(e => e.getAttribute('src'))") == [
        "vendor/leaflet/leaflet.js"]
    assert p.locator('link[rel=stylesheet]').evaluate_all("els => els.map(e => e.getAttribute('href'))") == [
        "vendor/leaflet/leaflet.css"]
    assert site.base + "/vendor/leaflet/leaflet.js" in a.requests
    assert site.base + "/vendor/leaflet/leaflet.css" in a.requests
    assert not [u for u in a.requests if "cdnjs.cloudflare.com" in u], a.requests
    # nothing but the site itself and the map tiles is ever asked for
    assert all(u.startswith(site.base + "/") or "tile.openstreetmap.org" in u for u in a.requests), a.requests
    assert p.evaluate("L.version") == "1.9.4"
    # the stylesheet really applied (Leaflet's panes are absolutely positioned)
    assert p.evaluate("getComputedStyle(document.querySelector('.leaflet-pane')).position") == "absolute"


def test_no_absolute_or_cdn_urls_in_the_shell():
    repo = App_repo()
    html = (repo / "index.html").read_text(encoding="utf-8")
    assert "cdnjs" not in html
    assert 'register("sw.js")' in html or "register('sw.js')" in html
    for f in ("leaflet.js", "leaflet.css", "LICENSE", "images/marker-icon.png", "images/marker-icon-2x.png",
              "images/marker-shadow.png", "images/layers.png", "images/layers-2x.png"):
        assert (repo / "vendor" / "leaflet" / f).is_file(), f
    dist = __import__("test_ui").LEAFLET_DIST
    for f in ("leaflet.js", "leaflet.css"):
        assert (repo / "vendor" / "leaflet" / f).read_bytes() == (dist / f).read_bytes()


def marker_loaded(app):
    p = app.page
    app.wait_centre(*START)
    app.settle(400)
    app.tap_map(250, 300)
    icon = p.locator("img.leaflet-marker-icon")
    expect(icon).to_have_count(1)
    p.wait_for_function(
        "(() => { const i = document.querySelector('img.leaflet-marker-icon'); return i.complete && i.naturalWidth > 0; })()")
    src = icon.evaluate("i => i.src")
    assert src.startswith(app.base + "/vendor/leaflet/images/marker-icon"), src
    shadow = p.locator("img.leaflet-marker-shadow")
    assert shadow.evaluate("i => i.src").startswith(app.base + "/vendor/leaflet/images/marker-shadow"), "shadow"


def test_default_marker_image_loads_in_test_mode(make_sw_app):
    a = make_sw_app(service_workers="block").open("")
    marker_loaded(a)


def test_leaflet_load_failure_shows_a_readable_message(make_sw_app, site):
    a = make_sw_app(service_workers="block")
    a.expect_errors = True  # the rest of the script cannot run without Leaflet
    a.ctx.route("**/vendor/leaflet/leaflet.js", lambda r: r.fulfill(status=404, body=""))
    a.page.goto(site.base + "/index.html")
    msg = a.page.locator("#no-leaflet")
    expect(msg).to_be_visible()
    text = msg.inner_text().lower()
    assert "signal" in text and "reload" in text
    box = msg.bounding_box()
    assert box["width"] >= 380 and box["height"] >= 800, "the message should cover the dead page"
    # it is on top: the centre of the screen is the message, not the map
    assert a.page.evaluate("document.elementFromPoint(195, 420).closest('#no-leaflet') !== null")


def test_message_is_hidden_when_leaflet_loads(make_sw_app):
    a = make_sw_app(service_workers="block").open("")
    expect(a.page.locator("#no-leaflet")).to_be_hidden()


# ============================================================== service worker
def test_service_worker_registers_under_the_sub_path_and_precaches(make_sw_app, site):
    a = make_sw_app().open("")
    sw_ready(a)
    p = a.page
    p.wait_for_function("navigator.serviceWorker.controller !== null")  # clients.claim()
    scope = p.evaluate("navigator.serviceWorker.ready.then(r => r.scope)")
    assert scope == site.base + "/"
    script = p.evaluate("navigator.serviceWorker.controller.scriptURL")
    assert script == site.base + "/sw.js"
    cached = set(p.evaluate(
        """(async () => { const out = [];
             for (const n of await caches.keys()) for (const r of await (await caches.open(n)).keys()) out.push(r.url);
             return out; })()"""))
    want = {site.base + "/" + f for f in (
        "", "index.html", "vendor/leaflet/leaflet.js", "vendor/leaflet/leaflet.css",
        "vendor/leaflet/images/marker-icon.png", "vendor/leaflet/images/marker-icon-2x.png",
        "vendor/leaflet/images/marker-shadow.png", "vendor/leaflet/images/layers.png",
        "vendor/leaflet/images/layers-2x.png")}
    assert want <= cached, want - cached


def test_offline_reload_of_a_password_hunt_with_wordle(make_sw_app, site):
    a = make_sw_app().open("")
    link = encrypted_link(a)
    a.open(link)
    p = a.page
    only_overlay(a, "#locked")
    unlock(a, PASSWORD)
    only_overlay(a, "#puzzle")
    sw_ready(a)

    go_offline(a, site)
    reload(a)
    assert p.evaluate("navigator.onLine") is False
    # password remembered: straight to the puzzle
    only_overlay(a, "#puzzle")
    solve_and_arrive(a)
    # and progress survives yet another offline reload
    reload(a)
    only_overlay(a, None)
    expect(p.locator("#play-clue")).to_have_text(C2["t"])


def test_offline_first_open_of_the_hunt_link_after_a_bare_online_visit(make_sw_app, site):
    maker = make_sw_app(service_workers="block").open("")
    link = encrypted_link(maker)

    a = make_sw_app().open("")  # online: the bare page only
    sw_ready(a)
    go_offline(a, site)
    p = a.page
    p.goto(site.base + "/index.html" + link, wait_until="commit")  # hash-only change: the app reloads itself
    p.wait_for_function("typeof mode !== 'undefined' && mode === 'play'")
    expect(p.locator("#locked")).to_be_visible()
    unlock(a, "wrong")
    expect(p.locator("#locked-err")).to_be_visible()
    unlock(a, PASSWORD)
    p.wait_for_function(BOOTED)
    solve_and_arrive(a)


def test_offline_open_of_the_directory_url_and_query_strings(make_sw_app, site):
    a = make_sw_app().open("")
    sw_ready(a)
    go_offline(a, site)
    for url in (site.base + "/", site.base + "/index.html?utm=1", site.base + "/?x=y#edit"):
        a.page.goto(url, wait_until="commit")
        a.page.wait_for_function(BOOTED)
    expect(a.page.locator("#editor-panel")).to_be_visible()


def test_offline_default_marker_comes_from_the_precache(make_sw_app, site):
    a = make_sw_app().open("")
    sw_ready(a)
    assert not [u for u in a.requests if "marker-icon" in u], "marker should not have been needed yet"
    go_offline(a, site)
    reload(a)
    marker_loaded(a)


def test_stalled_network_falls_back_to_the_cache_within_seconds(make_sw_app, site):
    a = make_sw_app().open("#hunt=" + b64u(HUNT))
    only_overlay(a, "#puzzle")
    sw_ready(a)
    go_offline(a, site, "stall")  # the phone thinks it has signal, but nothing answers
    t0 = time.time()
    reload(a)
    took = time.time() - t0
    only_overlay(a, "#puzzle")
    assert 2.5 < took < 15, took
    type_word(a, "apple")
    only_overlay(a, "#unlocked")


def test_a_response_cut_off_mid_body_falls_back_to_the_cache(make_sw_app, site):
    a = make_sw_app().open("#hunt=" + b64u(HUNT))
    only_overlay(a, "#puzzle")
    sw_ready(a)
    reload(a)  # now through the service worker
    p = a.page
    assert p.title() == "Offline hunt — Scavenger Hunt"
    site.set_index("</title>", " HALF</title>")
    site.mode = "cut"  # signal drops while each file is still arriving
    for _ in range(2):  # twice: the broken answer must not have replaced the cached copy
        del site.hits[:]
        reload(a)
        assert ("cut", "/" + SUBPATH + "/index.html") in site.hits
        only_overlay(a, "#puzzle")
        assert p.evaluate("document.title") == "Offline hunt — Scavenger Hunt"
        assert p.evaluate("L.version") == "1.9.4"
        a.settle(300)
    type_word(a, "apple")
    only_overlay(a, "#unlocked")
    # once the signal is back the newest copy arrives as usual
    site.mode = "up"
    reload(a)
    only_overlay(a, "#unlocked")
    assert "HALF" not in p.evaluate("document.title")  # the hunt's own title is showing
    assert "HALF" in p.evaluate("fetch('index.html').then(r => r.text())")


def test_online_reload_always_gets_the_newest_index(make_sw_app, site):
    a = make_sw_app().open("")
    sw_ready(a)
    p = a.page
    assert p.title() == "Scavenger Hunt"
    reload(a)  # a second visit, now through the service worker
    assert p.title() == "Scavenger Hunt"

    site.set_index("<title>Scavenger Hunt</title>", "<title>Scavenger Hunt FRESH-1</title>")
    reload(a)
    assert p.title() == "Scavenger Hunt FRESH-1"
    site.set_index("FRESH-1", "FRESH-2")
    p.goto(site.base + "/")  # the directory URL is just as fresh
    assert p.title() == "Scavenger Hunt FRESH-2"

    # the cache was refreshed too: offline now shows the newest copy, at both URLs
    p.wait_for_timeout(300)
    go_offline(a, site)
    reload(a)
    assert p.title() == "Scavenger Hunt FRESH-2"
    p.goto(site.base + "/index.html", wait_until="commit")
    p.wait_for_function(BOOTED)
    assert p.title() == "Scavenger Hunt FRESH-2"


def test_a_new_sw_and_index_reach_a_phone_that_has_the_old_ones(make_sw_app, site):
    a = make_sw_app().open("")
    sw_ready(a)
    sw = site.dir / "sw.js"
    sw.write_text(sw.read_text(encoding="utf-8") + "\n// changed tonight\n", encoding="utf-8")
    site.set_index("<title>Scavenger Hunt</title>", "<title>Scavenger Hunt NEW</title>")
    reload(a)
    assert a.page.title() == "Scavenger Hunt NEW"
    sw_ready(a)
    reload(a)
    assert a.page.title() == "Scavenger Hunt NEW"


def test_map_tiles_are_never_handled_by_the_service_worker(make_sw_app, site):
    a = make_sw_app().open("")
    sw_ready(a)
    del a.responses[:]
    reload(a)
    a.page.wait_for_function("document.querySelectorAll('img.leaflet-tile-loaded').length > 0")
    tiles = [r for r in a.responses if "tile.openstreetmap.org" in r.url]
    own = [r for r in a.responses if r.url.startswith(site.base)]
    assert tiles and own
    assert not any(r.from_service_worker for r in tiles)
    assert all(r.from_service_worker for r in own if not r.url.endswith("/sw.js")), [
        r.url for r in own if not r.from_service_worker]
    assert a.page.evaluate(
        "caches.keys().then(async ks => { for (const k of ks) for (const r of await (await caches.open(k)).keys())"
        " if (!r.url.startsWith(location.origin)) return false; return true; })")


def test_a_failed_precache_entry_does_not_stop_the_install(make_sw_app, site):
    (site.dir / "vendor" / "leaflet" / "images" / "layers-2x.png").unlink()
    a = make_sw_app().open("")
    sw_ready(a)
    go_offline(a, site)
    reload(a)
    marker_loaded(a)


def test_a_later_online_visit_fills_gaps_in_the_precache(make_sw_app, site):
    icon = site.dir / "vendor" / "leaflet" / "images" / "marker-icon-2x.png"
    hidden = icon.with_suffix(".away")
    icon.rename(hidden)  # the first visit cannot fetch it (think: signal dropped mid-install)
    a = make_sw_app().open("")
    sw_ready(a)
    hidden.rename(icon)
    reload(a)  # online again: the worker tops the cache up in the background
    a.page.wait_for_function(
        """u => caches.keys().then(async ks => { for (const k of ks)
             if (await (await caches.open(k)).match(u)) return true; return false; })""",
        arg=site.base + "/vendor/leaflet/images/marker-icon-2x.png")
    assert not [u for u in a.requests if "marker-icon" in u]
    go_offline(a, site)
    reload(a)
    marker_loaded(a)


def test_registration_failure_cannot_break_the_app(make_sw_app, site):
    (site.dir / "sw.js").unlink()  # register() will reject with a 404
    a = make_sw_app().open("#hunt=" + b64u(HUNT))
    only_overlay(a, "#puzzle")
    a.settle(500)
    assert a.page.evaluate("navigator.serviceWorker.controller") is None
    type_word(a, "apple")
    only_overlay(a, "#unlocked")
