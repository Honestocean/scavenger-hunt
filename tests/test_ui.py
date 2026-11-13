"""Headless browser tests for the scavenger hunt (index.html).

Written from the feature spec, not from the implementation. See README.md
in this directory for how to run them.
"""
import base64
import functools
import http.server
import json
import math
import os
import re
import threading
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

REPO = Path(__file__).resolve().parent.parent
LEAFLET_DIST = Path(os.environ.get("LEAFLET_DIST", "/home/claude/work/node_modules/leaflet/dist"))
CHROME = os.environ.get("CHROME_PATH", "/usr/bin/google-chrome")
SHOTS = Path(os.environ.get("SHOTS_DIR", "/home/claude/work/shots"))

CDN_PREFIX = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/"
BLANK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGO4+/gBAAVBAqGZNuZeAAAAAElFTkSuQmCC"  # 1x1 light grey
)
M_PER_DEG_LAT = 6371000 * math.pi / 180  # metres per degree of latitude

# A spot to play around (London) and one far away (Edinburgh).
HOME = (51.5, -0.12)
FAR = (55.9533, -3.1883)

# Test-only instrumentation, injected before the page's own scripts:
#  - counts the GPS fixes actually delivered to the app
#  - records which WebCrypto algorithms the app asks for
INIT_SCRIPT = """
(() => {
  window.__fixCount = 0;
  const geo = navigator.geolocation;
  const origWatch = geo.watchPosition.bind(geo);
  geo.watchPosition = (ok, err, opts) =>
    origWatch(p => { window.__fixCount++; ok(p); }, err, opts);
  window.__crypto = [];
  if (window.crypto && crypto.subtle) {
    for (const fn of ["importKey", "deriveKey", "deriveBits", "encrypt", "decrypt"]) {
      const orig = crypto.subtle[fn].bind(crypto.subtle);
      crypto.subtle[fn] = (...a) => {
        const alg = fn === "importKey" ? a[2] : a[0];
        const rec = { fn, name: (alg && alg.name) || alg };
        if (fn === "deriveKey") rec.derived = a[2] && a[2].name;
        window.__crypto.push(rec);
        return orig(...a);
      };
    }
  }
})();
"""


# ----------------------------------------------------------------- helpers
def b64u(obj):
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def b64u_bytes(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def decode_plain(payload):
    return json.loads(b64u_bytes(payload).decode("utf-8"))


def north(lat, metres):
    return lat + metres / M_PER_DEG_LAT


def payload_of(url, kind):
    marker = "#" + kind + "="
    assert marker in url, f"expected {marker} in {url!r}"
    return url.split(marker, 1)[1]


def hunt_obj(clues, **extra):
    d = {"v": 2, "clues": clues}
    d.update(extra)
    return d


class App:
    """One phone-sized browser context with a controllable GPS."""

    def __init__(self, browser, base, start=HOME, accuracy=5):
        self.base = base
        self.ctx = browser.new_context(
            viewport={"width": 390, "height": 844},
            has_touch=True,
            is_mobile=True,
            device_scale_factor=2,
            permissions=["geolocation", "clipboard-read", "clipboard-write"],
            geolocation={"latitude": start[0], "longitude": start[1], "accuracy": accuracy},
        )
        self.ctx.set_default_timeout(8000)
        self.ctx.add_init_script(INIT_SCRIPT)
        self.ctx.route("**/*", self._route)
        self.page = self.ctx.new_page()
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self._jitter = 0

    def _route(self, route):
        url = route.request.url
        if url.startswith(self.base):
            return route.continue_()
        if url.startswith(CDN_PREFIX):
            name = url[len(CDN_PREFIX):].split("?")[0]
            name = {"leaflet.min.css": "leaflet.css", "leaflet.min.js": "leaflet.js"}.get(name, name)
            f = LEAFLET_DIST / name
            if f.is_file():
                return route.fulfill(path=str(f))
            return route.fulfill(status=404, body="")
        if "tile.openstreetmap.org" in url:
            return route.fulfill(status=200, content_type="image/png", body=BLANK_PNG)
        return route.abort()

    # -- navigation
    def open(self, hash_="", wait_fix=True):
        self.page.goto(self.base + "/index.html" + hash_)
        self.page.wait_for_function("typeof map !== 'undefined' && typeof L !== 'undefined'")
        if wait_fix:
            self.page.wait_for_function("window.__fixCount >= 1 && myPos !== null")
        return self

    def open_url(self, url, wait_fix=True):
        assert url.startswith(self.base)
        return self.open(url.split("index.html", 1)[1] if "index.html" in url else url[len(self.base) + 1:], wait_fix)

    def reload(self):
        self.page.reload()
        self.page.wait_for_function("typeof map !== 'undefined' && window.__fixCount >= 1 && myPos !== null")

    # -- GPS
    def fix(self, lat, lng, accuracy=5):
        """Deliver exactly one new GPS fix and wait until the app has it."""
        self._jitter += 1
        lat = lat + (self._jitter % 7) * 2e-7  # ~2 cm steps so every fix is distinct
        before = self.page.evaluate("window.__fixCount")
        self.ctx.set_geolocation({"latitude": lat, "longitude": lng, "accuracy": accuracy})
        self.page.wait_for_function("n => window.__fixCount > n", arg=before)
        self.page.wait_for_timeout(120)
        after = self.page.evaluate("window.__fixCount")
        assert after == before + 1, f"expected one fix per set_geolocation, got {after - before}"
        pos = self.page.evaluate("myPos")
        assert abs(pos["lat"] - lat) < 1e-9 and pos["accuracy"] == accuracy
        return lat

    def fixes(self, n, lat, lng, accuracy=5):
        for _ in range(n):
            self.fix(lat, lng, accuracy)

    # -- map
    def centre(self):
        c = self.page.evaluate("(() => { const c = map.getCenter(); return [c.lat, c.lng]; })()")
        return tuple(c)

    def wait_centre(self, lat, lng, tol_m=3):
        self.page.wait_for_function(
            "([lat, lng, tol]) => map.getCenter().distanceTo([lat, lng]) <= tol", arg=[lat, lng, tol_m]
        )

    def dist_from_centre(self, lat, lng):
        return self.page.evaluate("([lat, lng]) => map.getCenter().distanceTo([lat, lng])", [lat, lng])

    def settle(self, ms=500):
        self.page.wait_for_timeout(ms)

    def drag_map(self, dx=120, dy=90):
        """Drag the map with a real touch gesture."""
        cdp = self.ctx.new_cdp_session(self.page)
        x, y = 195, 300
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        for i in range(1, 9):
            cdp.send(
                "Input.dispatchTouchEvent",
                {"type": "touchMove", "touchPoints": [{"x": x + dx * i / 8, "y": y + dy * i / 8}]},
            )
            self.page.wait_for_timeout(30)
        self.page.wait_for_timeout(250)  # pause so there is no fling
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        cdp.detach()
        self.settle(600)

    def tap_map(self, x, y):
        self.page.touchscreen.tap(x, y)
        self.page.wait_for_timeout(350)

    def latlng_at(self, x, y):
        return tuple(self.page.evaluate(
            "([x, y]) => { const p = map.containerPointToLatLng([x, y]); return [p.lat, p.lng]; }", [x, y]))

    def circles(self):
        """All metre-radius circles drawn on the map: [(lat, lng, radius)]."""
        return [tuple(c) for c in self.page.evaluate(
            """(() => { const out = [];
                 map.eachLayer(l => { if (l instanceof L.Circle) {
                   const p = l.getLatLng(); out.push([p.lat, p.lng, l.getRadius()]); } });
                 return out; })()""")]

    # -- editor
    def set_radius(self, metres):
        """Move the radius slider with the keyboard, like a user would."""
        slider = self.page.locator("#edit-radius")
        slider.focus()
        for _ in range(40):
            cur = int(slider.input_value())
            if cur == metres:
                return
            self.page.keyboard.press("ArrowRight" if cur < metres else "ArrowLeft")
        raise AssertionError(f"could not set slider to {metres}, stuck at {slider.input_value()}")

    def get_links(self):
        self.page.locator("#btn-share").click()
        expect(self.page.locator("#share")).to_be_visible()
        return self.page.locator("#share-url").input_value(), self.page.locator("#edit-url").input_value()

    def close(self):
        self.ctx.close()


def visible(app, selector):
    return app.page.locator(selector).is_visible()


def unlock(app, password):
    app.page.locator("#locked-pw").fill(password)
    app.page.locator("#locked-pw").press("Enter")


def find_clue(app, lat, lng, n=3):
    """Stand on a spot for n good fixes."""
    app.fixes(n, lat, lng, 5)


# ---------------------------------------------------------------- fixtures
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


TWO_CLUES = [
    {"t": "First", "lat": HOME[0], "lng": HOME[1]},
    {"t": "Second", "lat": north(HOME[0], 300), "lng": HOME[1]},
]
START = (north(HOME[0], -300), HOME[1])  # 300 m from clue 1, 600 m from clue 2


def build_in_editor(app, title="", password="", showdist=True, clues=(("One", 25), ("Two", 25))):
    """Create a hunt purely through the editor UI. Returns (player_url, edit_url, taps)."""
    app.open("#edit")
    app.settle(400)
    p = app.page
    if title:
        p.locator("#edit-title").fill(title)
    if password:
        p.locator("#edit-password").fill(password)
    if not showdist:
        p.locator("#edit-showdist").uncheck()
    spots = [(110, 190), (280, 300), (140, 330), (300, 180)]
    taps = []
    for (text, radius), (x, y) in zip(clues, spots):
        taps.append(app.latlng_at(x, y))
        app.tap_map(x, y)
        p.locator("#edit-clue-text").fill(text)
        app.set_radius(radius)
    player, edit = app.get_links()
    return player, edit, taps


# ===================================================== 1. edit link + restore
def test_get_links_shows_player_and_edit_links_with_copy_buttons(app):
    player, edit, _ = build_in_editor(app, title="Links")
    p = app.page
    assert re.fullmatch(re.escape(app.base) + r"/index\.html#hunt=[A-Za-z0-9_.\-]+", player)
    assert re.fullmatch(re.escape(app.base) + r"/index\.html#edit=[A-Za-z0-9_.\-]+", edit)
    expect(p.locator("#share-url")).to_be_visible()
    expect(p.locator("#edit-url")).to_be_visible()
    buttons = p.locator("#share button", has_text=re.compile("copy", re.I))
    assert buttons.count() == 2, "each link needs its own copy button"
    copied = []
    for i in range(2):
        buttons.nth(i).scroll_into_view_if_needed()
        buttons.nth(i).click()
        p.wait_for_timeout(150)
        copied.append(p.evaluate("navigator.clipboard.readText()"))
    assert sorted(copied) == sorted([player, edit])


def test_edit_link_restores_pins_text_radius_title_and_showdist(app, make_app):
    player, edit, taps = build_in_editor(
        app, title="Garden hunt", showdist=False, clues=(("Under the oak", 60), ("By the gate", 10)))
    original = decode_plain(payload_of(player, "hunt"))
    assert [c["t"] for c in original["clues"]] == ["Under the oak", "By the gate"]
    for c, (lat, lng) in zip(original["clues"], taps):
        assert abs(c["lat"] - lat) < 2e-5 and abs(c["lng"] - lng) < 2e-5

    b = make_app().open_url(edit)
    p = b.page
    expect(p.locator("#editor-panel")).to_be_visible()
    expect(p.locator("#edit-title")).to_have_value("Garden hunt")
    expect(p.locator("#edit-showdist")).not_to_be_checked()
    expect(p.locator("#edit-password")).to_have_value("")
    assert p.locator(".clue-pin").count() == 2
    # every pin is where it was put, with its circle
    circles = b.circles()
    for c, r in zip(original["clues"], (60, 10)):
        assert any(abs(lat - c["lat"]) < 1e-6 and abs(lng - c["lng"]) < 1e-6 and rad == r
                   for lat, lng, rad in circles), f"no {r} m circle at {c}"
    # each clue's text and radius come back in the form
    rows = p.locator("#clue-list li")
    assert rows.count() == 2
    for i, (text, r) in enumerate((("Under the oak", 60), ("By the gate", 10))):
        rows.nth(i).click()
        expect(p.locator("#edit-clue-text")).to_have_value(text)
        expect(p.locator("#edit-radius")).to_have_value(str(r))
    # and re-sharing without touching anything yields the same hunt
    player2, _ = b.get_links()
    assert decode_plain(payload_of(player2, "hunt")) == original


def test_edit_link_restores_checked_showdist_and_untitled(app, make_app):
    _, edit, _ = build_in_editor(app, showdist=True)
    b = make_app().open_url(edit)
    expect(b.page.locator("#edit-showdist")).to_be_checked()
    expect(b.page.locator("#edit-title")).to_have_value("")


def test_edit_link_restores_password_field(app, make_app):
    _, edit, _ = build_in_editor(app, title="Locked", password="open sesame")
    b = make_app().open_url(edit)
    expect(b.page.locator("#locked")).to_be_visible()
    expect(b.page.locator("#editor-panel")).to_be_hidden()
    unlock(b, "open sesame")
    expect(b.page.locator("#locked")).to_be_hidden()
    expect(b.page.locator("#editor-panel")).to_be_visible()
    expect(b.page.locator("#edit-password")).to_have_value("open sesame")
    expect(b.page.locator("#edit-title")).to_have_value("Locked")
    assert b.page.locator(".clue-pin").count() == 2
    # re-sharing keeps it locked
    player, edit2 = b.get_links()
    assert payload_of(player, "hunt").startswith("e.") and payload_of(edit2, "edit").startswith("e.")


def test_play_mode_has_no_edit_button(app):
    app.open("#hunt=" + b64u(hunt_obj(TWO_CLUES, title="T")))
    p = app.page
    expect(p.locator("#play-panel")).to_be_visible()
    labels = [b.inner_text() for b in p.locator("button").all() if b.is_visible()]
    assert labels, "play mode should show some buttons"
    for label in labels:
        assert not re.search(r"edit|create", label, re.I), f"edit-ish button in play mode: {label!r}"
    assert not p.locator("#btn-edit").is_visible()
    assert not p.locator("#editor-panel").is_visible()


# ============================================================= 2. map follow
def _assert_button(app, following):
    btn = app.page.locator(".btn-center:visible")
    assert btn.count() == 1
    cls = (btn.get_attribute("class") or "").split()
    if following:
        assert btn.inner_text().strip() == "Following you ✓"
        assert "primary" not in cls
    else:
        assert btn.inner_text().strip() == "Centre on me"
        assert "primary" in cls


@pytest.mark.parametrize("mode", ["test", "play"])
def test_map_follows_until_dragged_and_recentres_on_button(app, mode):
    start = START
    app.ctx.set_geolocation({"latitude": start[0], "longitude": start[1], "accuracy": 5})
    app.open("" if mode == "test" else "#hunt=" + b64u(hunt_obj(TWO_CLUES)))
    app.wait_centre(*start)
    assert app.page.evaluate("follow") is True
    _assert_button(app, following=True)

    # follows successive fixes
    for i in (1, 2):
        lat = app.fix(north(start[0], 40 * i), start[1])
        app.wait_centre(lat, start[1])

    # dragging turns following off
    before = app.centre()
    app.drag_map()
    after_drag = app.centre()
    assert app.dist_from_centre(*before) > 20, "the drag should have moved the map"
    assert app.page.evaluate("follow") is False
    _assert_button(app, following=False)

    # ... and it stays off across further fixes
    for i in (3, 4, 5):
        lat = app.fix(north(start[0], 40 * i), start[1])
        app.settle(450)
        assert app.dist_from_centre(*after_drag) < 0.5, "map moved back while following was off"
        assert app.page.evaluate("follow") is False
    _assert_button(app, following=False)

    # the button recentres and resumes following
    app.page.locator(".btn-center:visible").click()
    app.wait_centre(lat, start[1])
    assert app.page.evaluate("follow") is True
    _assert_button(app, following=True)
    lat = app.fix(north(start[0], 240), start[1])
    app.wait_centre(lat, start[1])


def test_editor_starts_with_following_off(app):
    app.open("#edit")
    app.settle(300)
    assert app.page.evaluate("follow") is False
    _assert_button(app, following=False)
    c = app.centre()
    for i in (1, 2, 3):
        app.fix(north(HOME[0], 60 * i), HOME[1])
        app.settle(350)
    assert app.dist_from_centre(*c) < 0.5, "editor map must not chase the user"
    # the editor's button still works
    app.page.locator(".btn-center:visible").click()
    pos = app.page.evaluate("myPos")
    app.wait_centre(pos["lat"], pos["lng"])
    assert app.page.evaluate("follow") is True
    _assert_button(app, following=True)


@pytest.mark.parametrize("locked", [False, True], ids=["plain", "password"])
def test_edit_link_shows_the_hunts_pins_not_the_user(app, make_app, locked):
    # Build the hunt in Edinburgh, then open its edit link while standing in London.
    app.ctx.set_geolocation({"latitude": FAR[0], "longitude": FAR[1], "accuracy": 5})
    player, edit, taps = build_in_editor(app, password="pw" if locked else "")
    b = make_app(start=HOME)
    b.open_url(edit)
    if locked:
        b.fix(north(HOME[0], 5), HOME[1])
        unlock(b, "pw")
        expect(b.page.locator("#editor-panel")).to_be_visible()
    b.settle(700)

    def check():
        bounds_ok = b.page.evaluate(
            "pts => pts.every(p => map.getBounds().contains(p))", [list(t) for t in taps])
        assert bounds_ok, "the hunt's pins should be in view"
        assert b.dist_from_centre(*HOME) > 100_000, "map jumped to the user's position"
        assert b.dist_from_centre(*FAR) < 2_000

    check()
    for i in (1, 2, 3):
        b.fix(north(HOME[0], 10 * i), HOME[1])
        b.settle(350)
    check()
    assert b.page.evaluate("follow") is False
    _assert_button(b, following=False)


# ====================================================== 3. per-clue radius
def test_radius_slider_range_step_and_default(app):
    app.open("#edit")
    app.settle(300)
    app.tap_map(150, 250)
    s = app.page.locator("#edit-radius")
    expect(s).to_be_visible()
    assert s.get_attribute("type") == "range"
    assert (s.get_attribute("min"), s.get_attribute("max"), s.get_attribute("step")) == ("10", "100", "5")
    assert s.input_value() == "25"
    s.focus()
    app.page.keyboard.press("ArrowRight")
    assert s.input_value() == "30"
    app.page.keyboard.press("Home")
    assert s.input_value() == "10"
    app.page.keyboard.press("End")
    assert s.input_value() == "100"


def test_editor_draws_a_circle_of_each_clues_radius(app):
    app.open("#edit")
    app.settle(300)
    spots = [(110, 190), (280, 300)]
    lls = [app.latlng_at(*s) for s in spots]

    def circle_at(ll):
        found = [r for lat, lng, r in app.circles() if abs(lat - ll[0]) < 2e-5 and abs(lng - ll[1]) < 2e-5]
        assert len(found) == 1, f"expected one circle at {ll}, got {found}"
        return found[0]

    app.tap_map(*spots[0])
    assert circle_at(lls[0]) == 25  # default
    app.set_radius(80)
    assert circle_at(lls[0]) == 80
    app.tap_map(*spots[1])
    assert circle_at(lls[1]) == 25  # a new clue starts at the default again
    app.set_radius(15)
    assert circle_at(lls[1]) == 15
    assert circle_at(lls[0]) == 80  # the first one is untouched
    assert app.page.locator("#edit-radius").input_value() == "15"


def test_radius_is_saved_in_both_links(app):
    player, edit, _ = build_in_editor(app, clues=(("A", 60), ("B", 25), ("C", 10)))
    for d in (decode_plain(payload_of(player, "hunt")), decode_plain(payload_of(edit, "edit"))):
        assert [c.get("r", 25) for c in d["clues"]] == [60, 25, 10]


def test_play_uses_each_clues_own_radius(app):
    clues = [
        {"t": "Small", "lat": HOME[0], "lng": HOME[1], "r": 10},
        {"t": "Big", "lat": north(HOME[0], 500), "lng": HOME[1], "r": 60},
    ]
    app.ctx.set_geolocation({"latitude": START[0], "longitude": START[1], "accuracy": 5})
    app.open("#hunt=" + b64u(hunt_obj(clues)))
    p = app.page
    expect(p.locator("#play-clue")).to_have_text("Small")
    # 18 m away: inside the default 25 m but outside this clue's 10 m
    app.fixes(5, north(HOME[0], 18), HOME[1])
    assert not visible(app, "#found") and not visible(app, "#finished")
    # 8 m away: inside
    app.fixes(3, north(HOME[0], 8), HOME[1])
    expect(p.locator("#found")).to_be_visible()
    expect(p.locator("#found-clue")).to_have_text("Big")
    p.locator("#btn-next").click()
    expect(p.locator("#found")).to_be_hidden()
    # 70 m from the second clue: outside its 60 m
    app.fixes(4, north(HOME[0], 500 - 70), HOME[1])
    assert not visible(app, "#finished")
    # 50 m away: outside the default 25 m but inside this clue's 60 m
    app.fixes(3, north(HOME[0], 500 - 50), HOME[1])
    expect(p.locator("#finished")).to_be_visible()


def test_radius_set_in_editor_is_used_in_play(app, make_app):
    player, _, taps = build_in_editor(app, clues=(("Wide", 100), ("Narrow", 10)))
    d = decode_plain(payload_of(player, "hunt"))
    c1, c2 = d["clues"]
    b = make_app(start=(north(c1["lat"], 600), c1["lng"]))
    b.open_url(player)
    b.fixes(3, north(c1["lat"], 90), c1["lng"])  # 90 m: only inside a 100 m radius
    expect(b.page.locator("#found")).to_be_visible()
    expect(b.page.locator("#found-clue")).to_have_text("Narrow")
    b.page.locator("#btn-next").click()
    b.fixes(4, north(c2["lat"], 15), c2["lng"])  # 15 m: outside a 10 m radius
    assert not visible(b, "#finished")
    b.fixes(3, north(c2["lat"], 5), c2["lng"])
    expect(b.page.locator("#finished")).to_be_visible()


def test_arrival_needs_three_consecutive_fixes(app):
    app.ctx.set_geolocation({"latitude": START[0], "longitude": START[1], "accuracy": 5})
    app.open("#hunt=" + b64u(hunt_obj(TWO_CLUES)))
    p = app.page
    inside = (north(HOME[0], 5), HOME[1])
    outside = (north(HOME[0], 80), HOME[1])
    app.fixes(2, *inside)
    assert not visible(app, "#found"), "arrived after only 2 fixes"
    app.fix(*outside)  # breaks the run
    app.fixes(2, *inside)
    assert not visible(app, "#found"), "fixes were not consecutive"
    app.fix(*inside)
    expect(p.locator("#found")).to_be_visible()


def test_arrival_ignores_fixes_with_accuracy_worse_than_35m(app):
    app.ctx.set_geolocation({"latitude": START[0], "longitude": START[1], "accuracy": 5})
    app.open("#hunt=" + b64u(hunt_obj(TWO_CLUES)))
    p = app.page
    inside = (north(HOME[0], 5), HOME[1])
    app.fixes(5, *inside, accuracy=36)
    assert not visible(app, "#found"), "36 m accuracy must not count"
    app.fixes(2, *inside, accuracy=35)
    app.fix(*inside, accuracy=60)  # an inaccurate fix breaks the run
    app.fixes(2, *inside, accuracy=35)
    assert not visible(app, "#found"), "an inaccurate fix must reset the count"
    app.fix(*inside, accuracy=35)
    expect(p.locator("#found")).to_be_visible()


def test_found_shows_next_clue_then_finished_after_last(app):
    clues = TWO_CLUES + [{"t": "Third", "lat": north(HOME[0], 600), "lng": HOME[1]}]
    app.ctx.set_geolocation({"latitude": START[0], "longitude": START[1], "accuracy": 5})
    app.open("#hunt=" + b64u(hunt_obj(clues)))
    p = app.page
    expect(p.locator("#play-clue")).to_have_text("First")
    find_clue(app, clues[0]["lat"], clues[0]["lng"])
    expect(p.locator("#found")).to_be_visible()
    expect(p.locator("#found-clue")).to_have_text("Second")
    assert not visible(app, "#finished")
    p.locator("#btn-next").click()
    expect(p.locator("#found")).to_be_hidden()
    expect(p.locator("#play-clue")).to_have_text("Second")
    find_clue(app, clues[1]["lat"], clues[1]["lng"])
    expect(p.locator("#found")).to_be_visible()
    expect(p.locator("#found-clue")).to_have_text("Third")
    p.locator("#btn-next").click()
    find_clue(app, clues[2]["lat"], clues[2]["lng"])
    expect(p.locator("#finished")).to_be_visible()
    assert not visible(app, "#found")


def test_next_clue_needs_three_new_fixes_even_if_already_inside_it(app):
    """Two clues whose circles overlap: the fix that found clue 1 is not a fix for clue 2."""
    clues = [
        {"t": "First", "lat": HOME[0], "lng": HOME[1], "r": 25},
        {"t": "Second", "lat": north(HOME[0], 20), "lng": HOME[1], "r": 100},
    ]
    app.ctx.set_geolocation({"latitude": START[0], "longitude": START[1], "accuracy": 5})
    app.open("#hunt=" + b64u(hunt_obj(clues)))
    p = app.page
    find_clue(app, HOME[0], HOME[1])
    expect(p.locator("#found")).to_be_visible()
    p.locator("#btn-next").click()
    expect(p.locator("#play-clue")).to_have_text("Second")
    app.settle(300)
    assert not visible(app, "#finished")
    app.fixes(2, HOME[0], HOME[1])
    assert not visible(app, "#finished"), "second clue triggered after only 2 GPS fixes"
    app.fix(HOME[0], HOME[1])
    expect(p.locator("#finished")).to_be_visible()


# =========================================================== 4. password
SECRET_CLUES = [
    {"t": "Behind the bandstand", "lat": HOME[0], "lng": HOME[1], "r": 30},
    {"t": "Under the bridge", "lat": north(HOME[0], 300), "lng": HOME[1]},
]


def lock_via_editor(app, password, clues=SECRET_CLUES, **extra):
    """Open a plain edit link, set a password, and return the (player, edit) links."""
    app.open("#edit=" + b64u(hunt_obj(clues, **extra)))
    expect(app.page.locator("#editor-panel")).to_be_visible()
    app.page.locator("#edit-password").fill(password)
    return app.get_links()


def test_password_encrypts_both_links(app):
    player, edit = lock_via_editor(app, "Treasure", title="Top secret title")
    for url, kind in ((player, "hunt"), (edit, "edit")):
        payload = payload_of(url, kind)
        assert payload.startswith("e."), payload[:20]
        # nothing readable by base64-decoding the whole thing or any piece of it
        blobs = []
        for piece in [payload, payload[2:], payload.replace(".", "")] + payload.split("."):
            for skip in range(4):
                try:
                    blobs.append(b64u_bytes(piece[skip:]))
                except Exception:
                    pass
        assert blobs
        haystack = b"\n".join(blobs)
        for needle in (b"bandstand", b"bridge", b"Behind", b"clues", b'"t"', b"lat", b"51.5", b"-0.12",
                       b"secret", b"Treasure", b"treasure"):
            assert needle not in haystack, f"{needle!r} is recoverable from the {kind} link"
        assert "51.5" not in url.split("#")[1] and "bandstand" not in url
    calls = app.page.evaluate("window.__crypto")
    assert any(c["fn"] == "encrypt" and c["name"] == "AES-GCM" for c in calls), calls
    assert any(c["fn"] in ("deriveKey", "deriveBits") and c["name"] == "PBKDF2" for c in calls), calls


def test_no_password_gives_plain_base64url_json(app):
    app.open("#edit=" + b64u(hunt_obj(SECRET_CLUES, title="Open")))
    player, edit = app.get_links()
    for url, kind in ((player, "hunt"), (edit, "edit")):
        payload = payload_of(url, kind)
        assert not payload.startswith("e.")
        assert re.fullmatch(r"[A-Za-z0-9_\-]+", payload), "must be unpadded base64url"
        d = decode_plain(payload)
        assert d["title"] == "Open"
        assert [(c["t"], c["lat"], c["lng"], c.get("r", 25)) for c in d["clues"]] == [
            ("Behind the bandstand", HOME[0], HOME[1], 30), ("Under the bridge", SECRET_CLUES[1]["lat"], HOME[1], 25)]
    assert not any(c["fn"] == "encrypt" for c in app.page.evaluate("window.__crypto"))


def test_blank_password_of_only_spaces_is_no_password(app):
    player, _ = lock_via_editor(app, "   ")
    assert decode_plain(payload_of(player, "hunt"))["clues"][0]["t"] == "Behind the bandstand"


def test_locked_link_wrong_then_right_password(app, make_app):
    player, _ = lock_via_editor(app, "Treasure")
    b = make_app(start=START)
    b.open_url(player)
    p = b.page
    expect(p.locator("#locked")).to_be_visible()
    expect(p.locator("#locked-err")).to_be_hidden()
    expect(p.locator("#play-panel")).to_be_hidden()
    assert "bandstand" not in p.locator("body").inner_text()

    unlock(b, "wrong")
    expect(p.locator("#locked-err")).to_be_visible()
    expect(p.locator("#locked")).to_be_visible()
    expect(p.locator("#play-panel")).to_be_hidden()
    assert "bandstand" not in p.locator("body").inner_text()

    # the Unlock button works as well as Enter
    p.locator("#locked-pw").fill("Treasure")
    p.locator("#btn-unlock").click()
    expect(p.locator("#locked")).to_be_hidden()
    expect(p.locator("#play-panel")).to_be_visible()
    expect(p.locator("#play-clue")).to_have_text("Behind the bandstand")
    calls = p.evaluate("window.__crypto")
    assert any(c["fn"] == "decrypt" and c["name"] == "AES-GCM" for c in calls), calls

    # play really starts: radius 30 from the encrypted link is honoured
    b.fixes(4, north(HOME[0], 40), HOME[1])
    assert not visible(b, "#found")
    b.fixes(3, north(HOME[0], 28), HOME[1])
    expect(p.locator("#found")).to_be_visible()
    expect(p.locator("#found-clue")).to_have_text("Under the bridge")


def test_unlocking_while_standing_on_the_clue_still_needs_three_fixes(app, make_app):
    player, _ = lock_via_editor(app, "pw")
    b = make_app(start=HOME)
    b.open_url(player)
    b.fixes(3, HOME[0], HOME[1])  # fixes that arrive while the hunt is still locked
    unlock(b, "pw")
    expect(b.page.locator("#play-panel")).to_be_visible()
    b.settle(300)
    assert not visible(b, "#found"), "arrival triggered by fixes from before the hunt started"
    b.fixes(2, HOME[0], HOME[1])
    assert not visible(b, "#found"), "arrival after only 2 fixes since unlocking"
    b.fix(HOME[0], HOME[1])
    expect(b.page.locator("#found")).to_be_visible()


@pytest.mark.parametrize("typed", ["secret word", "SECRET WORD", "  Secret   Word  ", "sEcReT  word", "Secret Word"])
def test_password_ignores_case_and_extra_spaces(app, make_app, typed):
    player, _ = lock_via_editor(app, " Secret  Word ")
    b = make_app()
    b.open_url(player)
    expect(b.page.locator("#locked")).to_be_visible()
    unlock(b, typed)
    expect(b.page.locator("#locked")).to_be_hidden()
    expect(b.page.locator("#play-clue")).to_have_text("Behind the bandstand")


def test_password_spaces_inside_words_still_matter(app, make_app):
    player, _ = lock_via_editor(app, "secret word")
    b = make_app()
    b.open_url(player)
    unlock(b, "secretword")
    expect(b.page.locator("#locked-err")).to_be_visible()
    expect(b.page.locator("#locked")).to_be_visible()


def test_reload_does_not_ask_for_password_again(app, make_app):
    player, edit = lock_via_editor(app, "Treasure")
    b = make_app()
    b.open_url(player)
    unlock(b, "treasure")
    expect(b.page.locator("#play-panel")).to_be_visible()
    b.reload()
    expect(b.page.locator("#play-panel")).to_be_visible()
    expect(b.page.locator("#locked")).to_be_hidden()
    expect(b.page.locator("#play-clue")).to_have_text("Behind the bandstand")
    stored = b.page.evaluate("JSON.stringify(Object.entries(localStorage))")
    assert "treasure" in stored.lower(), "spec says the password is remembered in localStorage"


def test_wrong_password_is_not_remembered(app, make_app):
    player, _ = lock_via_editor(app, "Treasure")
    b = make_app()
    b.open_url(player)
    unlock(b, "nope")
    expect(b.page.locator("#locked-err")).to_be_visible()
    b.reload()
    expect(b.page.locator("#locked")).to_be_visible()
    expect(b.page.locator("#play-panel")).to_be_hidden()


def test_locked_edit_link_wrong_then_right(app, make_app):
    _, edit = lock_via_editor(app, "Treasure", title="Kept", showDist=False)
    b = make_app()
    b.open_url(edit)
    p = b.page
    expect(p.locator("#locked")).to_be_visible()
    unlock(b, "bad")
    expect(p.locator("#locked-err")).to_be_visible()
    expect(p.locator("#editor-panel")).to_be_hidden()
    unlock(b, "TREASURE")
    expect(p.locator("#locked")).to_be_hidden()
    expect(p.locator("#editor-panel")).to_be_visible()
    expect(p.locator("#edit-title")).to_have_value("Kept")
    expect(p.locator("#edit-showdist")).not_to_be_checked()
    assert p.locator("#edit-password").input_value().strip().lower() == "treasure"
    rows = p.locator("#clue-list li")
    rows.nth(0).click()
    expect(p.locator("#edit-clue-text")).to_have_value("Behind the bandstand")
    expect(p.locator("#edit-radius")).to_have_value("30")
    rows.nth(1).click()
    expect(p.locator("#edit-clue-text")).to_have_value("Under the bridge")
    expect(p.locator("#edit-radius")).to_have_value("25")


# ================================================ 5. backward compatibility
OLD = {"v": 1, "title": "Old", "clues": [{"t": "First", "lat": 51.5, "lng": -0.12},
                                         {"t": "Second", "lat": 51.501, "lng": -0.12}]}


def test_old_link_plays_with_default_25m_radius(app):
    payload = base64.urlsafe_b64encode(json.dumps(OLD).encode()).decode().rstrip("=")
    app.ctx.set_geolocation({"latitude": START[0], "longitude": START[1], "accuracy": 5})
    app.open("#hunt=" + payload)
    p = app.page
    expect(p.locator("#play-panel")).to_be_visible()
    expect(p.locator("#locked")).to_be_hidden()
    expect(p.locator("#play-clue")).to_have_text("First")
    expect(p.locator("#play-dist-row")).to_be_visible()
    assert "Old" in p.title()
    app.fixes(4, north(51.5, 30), -0.12)  # 30 m: outside the default radius
    assert not visible(app, "#found")
    app.fixes(3, north(51.5, 20), -0.12)  # 20 m: inside
    expect(p.locator("#found")).to_be_visible()
    expect(p.locator("#found-clue")).to_have_text("Second")
    p.locator("#btn-next").click()
    app.fixes(3, 51.501, -0.12)
    expect(p.locator("#finished")).to_be_visible()


def test_old_link_showdist_false_hides_distance(app):
    app.open("#hunt=" + b64u(dict(OLD, showDist=False)))
    expect(app.page.locator("#play-panel")).to_be_visible()
    expect(app.page.locator("#play-clue")).to_have_text("First")
    expect(app.page.locator("#play-dist-row")).to_be_hidden()


def test_old_link_opens_in_editor_with_default_radius(app):
    app.open("#edit=" + b64u(OLD))
    p = app.page
    expect(p.locator("#edit-title")).to_have_value("Old")
    expect(p.locator("#edit-showdist")).to_be_checked()
    p.locator("#clue-list li").nth(1).click()
    expect(p.locator("#edit-clue-text")).to_have_value("Second")
    expect(p.locator("#edit-radius")).to_have_value("25")


# ========================================================= 6. regressions
def test_test_mode_target_distance_and_arrival(app):
    app.open("")
    p = app.page
    expect(p.locator("#test-panel")).to_be_visible()
    app.wait_centre(*HOME)
    app.settle(400)
    x, y = 250, 300
    t_lat, t_lng = app.latlng_at(x, y)
    app.tap_map(x, y)
    expected = p.evaluate("([a, b]) => L.latLng(myPos.lat, myPos.lng).distanceTo(a)", [[t_lat, t_lng], 0])
    assert expected > 30, "target should be outside the radius for this test"
    expect(p.locator("#distance")).to_be_visible()
    shown = p.locator("#distance").inner_text()
    m = re.fullmatch(r"(\d+) m", shown)
    assert m and abs(int(m.group(1)) - expected) <= 2, (shown, expected)
    tgt = p.evaluate("target")
    assert abs(tgt["lat"] - t_lat) < 2e-5 and abs(tgt["lng"] - t_lng) < 2e-5
    assert not visible(app, "#arrived")
    # walk there
    app.fixes(2, t_lat, t_lng)
    assert not visible(app, "#arrived"), "arrived after only 2 fixes"
    assert re.fullmatch(r"[0-2] m", p.locator("#distance").inner_text())
    app.fix(t_lat, t_lng)
    expect(p.locator("#arrived")).to_be_visible()


def test_test_mode_target_under_my_feet_needs_three_new_fixes(app):
    app.open("")
    app.wait_centre(*HOME)
    app.settle(400)
    app.tap_map(195, 422)  # screen centre = where I am standing
    d = app.page.evaluate("L.latLng(myPos.lat, myPos.lng).distanceTo([target.lat, target.lng])")
    assert d < 15
    app.settle(300)
    assert not visible(app, "#arrived")
    app.fixes(2, *HOME)
    assert not visible(app, "#arrived"), "arrived after only 2 GPS fixes"
    app.fix(*HOME)
    expect(app.page.locator("#arrived")).to_be_visible()


def test_progress_is_saved_and_resumed_on_reload(app):
    app.ctx.set_geolocation({"latitude": START[0], "longitude": START[1], "accuracy": 5})
    app.open("#hunt=" + b64u(hunt_obj(TWO_CLUES)))
    p = app.page
    find_clue(app, TWO_CLUES[0]["lat"], TWO_CLUES[0]["lng"])
    expect(p.locator("#found")).to_be_visible()
    # move away, then reload (even without tapping "Let's go")
    app.fix(*START)
    app.reload()
    expect(p.locator("#play-clue")).to_have_text("Second")
    expect(p.locator("#play-progress")).to_contain_text("2 of 2")
    assert not visible(app, "#finished")
    find_clue(app, TWO_CLUES[1]["lat"], TWO_CLUES[1]["lng"])
    expect(p.locator("#finished")).to_be_visible()
    app.fix(*START)
    app.reload()
    expect(p.locator("#finished")).to_be_visible()  # a finished hunt stays finished


def test_progress_is_per_hunt(app):
    app.ctx.set_geolocation({"latitude": START[0], "longitude": START[1], "accuracy": 5})
    app.open("#hunt=" + b64u(hunt_obj(TWO_CLUES)))
    find_clue(app, TWO_CLUES[0]["lat"], TWO_CLUES[0]["lng"])
    expect(app.page.locator("#found")).to_be_visible()
    app.fix(*START)
    other = [dict(c, t="Other " + c["t"]) for c in TWO_CLUES]
    app.open("#hunt=" + b64u(hunt_obj(other)))
    expect(app.page.locator("#play-clue")).to_have_text("Other First")


def test_restart_hunt_needs_two_taps(app):
    app.ctx.set_geolocation({"latitude": START[0], "longitude": START[1], "accuracy": 5})
    app.open("#hunt=" + b64u(hunt_obj(TWO_CLUES)))
    p = app.page
    find_clue(app, TWO_CLUES[0]["lat"], TWO_CLUES[0]["lng"])
    p.locator("#btn-next").click()
    app.fix(*START)
    expect(p.locator("#play-clue")).to_have_text("Second")
    btn = p.locator("#btn-play-reset")
    expect(btn).to_have_text("Restart hunt")
    # one tap alone does nothing lasting
    btn.click()
    app.settle(300)
    expect(p.locator("#play-clue")).to_have_text("Second")
    app.reload()
    expect(p.locator("#play-clue")).to_have_text("Second")
    # an armed button disarms itself after a few seconds
    btn.click()
    expect(btn).not_to_have_text("Restart hunt")
    expect(btn).to_have_text("Restart hunt", timeout=6000)
    expect(p.locator("#play-clue")).to_have_text("Second")
    # two taps restart
    btn.click()
    btn.click()
    expect(p.locator("#play-clue")).to_have_text("First")
    expect(p.locator("#play-progress")).to_contain_text("1 of 2")
    app.reload()
    expect(p.locator("#play-clue")).to_have_text("First")


NON_ASCII = ("Cherchez près du café ☕ — où l'été s'achève", "宝探し 🏴‍☠️🎉 Ünïcödé ñandú")


@pytest.mark.parametrize("password", ["", "pässwörd 🔑"], ids=["plain", "encrypted"])
def test_non_ascii_text_round_trips(app, make_app, password):
    title = "Été 🌞 Zoë's hunt"
    player, edit, _ = build_in_editor(
        app, title=title, password=password, clues=((NON_ASCII[0], 25), (NON_ASCII[1], 40)))
    assert payload_of(player, "hunt").startswith("e.") == bool(password)
    assert re.fullmatch(r"[A-Za-z0-9_.\-]+", payload_of(player, "hunt")), "payload must be URL-safe ASCII"

    # editor round trip
    b = make_app()
    b.open_url(edit)
    if password:
        expect(b.page.locator("#locked")).to_be_visible()
        unlock(b, "PÄSSWÖRD   🔑")
    expect(b.page.locator("#edit-title")).to_have_value(title)
    rows = b.page.locator("#clue-list li")
    for i, text in enumerate(NON_ASCII):
        rows.nth(i).click()
        expect(b.page.locator("#edit-clue-text")).to_have_value(text)
    expect(b.page.locator("#edit-radius")).to_have_value("40")

    # play round trip
    c = make_app()
    c.open_url(player)
    if password:
        unlock(c, password)
    expect(c.page.locator("#play-clue")).to_have_text(NON_ASCII[0])
    assert title in c.page.title()
    if not password:
        d = decode_plain(payload_of(player, "hunt"))
        assert d["title"] == title and [x["t"] for x in d["clues"]] == list(NON_ASCII)
        first = d["clues"][0]
        c.fixes(3, first["lat"], first["lng"])
        expect(c.page.locator("#found-clue")).to_have_text(NON_ASCII[1])


def test_unreadable_links_do_not_crash(app):
    for h in ("#hunt=!!!notbase64", "#hunt=e.AAAA.BBBB", "#edit=%%%"):
        app.open(h)
        assert visible(app, "#test-panel") or visible(app, "#editor-panel") or visible(app, "#locked")


# ===================================================== follow-up fixes
def test_editor_action_buttons_stay_on_screen_with_a_clue_selected(app):
    app.open("#edit")
    app.settle(400)
    p = app.page
    p.locator("#edit-title").fill("A title")
    for x, y in ((110, 190), (280, 300), (140, 330)):
        app.tap_map(x, y)
        p.locator("#edit-clue-text").fill("Some clue text")
    p.locator("#edit-clue-text").blur()
    app.settle(200)
    for sel in ("#btn-share", "#btn-edit-center", "#btn-exit-editor"):
        box = p.locator(sel).bounding_box()
        panel = p.locator("#editor-panel").bounding_box()
        assert box["y"] >= panel["y"], sel
        assert box["y"] + box["height"] <= min(844, panel["y"] + panel["height"]) + 0.5, sel
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390, sel
        # and it is really tappable, not covered by something else
        hit = p.evaluate(
            "([x, y, id]) => { const e = document.elementFromPoint(x, y); return !!e && (e.id === id || !!e.closest('#' + id)); }",
            [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, sel[1:]])
        assert hit, sel


def test_editor_map_can_still_be_tapped_above_the_panel(app):
    app.open("#edit")
    app.settle(400)
    p = app.page
    for x, y in ((110, 190), (280, 300), (140, 330)):
        app.tap_map(x, y)
    panel = p.locator("#editor-panel").bounding_box()
    assert panel["y"] >= 844 * 0.35, "editor panel must leave room to tap the map"


@pytest.mark.parametrize("h", ["#hunt=!!!notbase64", "#hunt=e.AAAA.BBBB", "#hunt=e.AAAA.BBBB.CC.DD",
                               "#hunt=" + "eyJ2IjoxfQ"], ids=["garbage", "enc-3-parts", "enc-5-parts", "no-clues"])
def test_unreadable_play_link_says_so(app, h):
    app.open(h)
    p = app.page
    expect(p.locator("#locked")).to_be_hidden()
    expect(p.locator("#test-panel")).to_be_visible()
    expect(p.locator("#link-error")).to_be_visible()
    expect(p.locator("#link-error")).to_contain_text("link")


def test_good_links_show_no_link_error(app):
    app.open("")
    expect(app.page.locator("#link-error")).to_be_hidden()


def test_null_radius_in_link_defaults_to_25m(app):
    clues = [{"t": "First", "lat": 51.5, "lng": -0.12, "r": None}]
    app.ctx.set_geolocation({"latitude": START[0], "longitude": START[1], "accuracy": 5})
    app.open("#hunt=" + b64u(hunt_obj(clues)))
    assert app.page.evaluate("targetRadius") == 25


def test_overlays_fully_cover_what_is_behind_them(app):
    app.open("#edit")
    app.settle(300)
    app.tap_map(110, 190)
    app.get_links()
    bg = app.page.evaluate("getComputedStyle(document.getElementById('share')).backgroundColor")
    # opaque: rgb(...) or rgba(..., 1)
    assert bg.startswith("rgb(") or bg.replace(" ", "").endswith(",1)"), bg


# ===================================================== review findings
def test_damaged_encrypted_link_is_reported_not_treated_as_wrong_password(app):
    # right shape (4 parts) but salt and IV are the wrong length
    app.open("#hunt=e.AAAA.BBBB.CCCCCCCCCCCCCCCCCCCCCCCC")
    p = app.page
    expect(p.locator("#locked")).to_be_hidden()
    expect(p.locator("#link-error")).to_be_visible()


def test_password_prompt_sits_high_enough_to_clear_the_keyboard(app, make_app):
    player, _ = lock_via_editor(app, "secret word")
    other = make_app()
    other.open_url(player, wait_fix=False)
    p = other.page
    expect(p.locator("#locked")).to_be_visible()
    p.locator("#locked-pw").fill("nope")
    p.locator("#btn-unlock").click()
    expect(p.locator("#locked-err")).to_be_visible()
    err = p.locator("#locked-err").bounding_box()
    inp = p.locator("#locked-pw").bounding_box()
    btn = p.locator("#btn-unlock").bounding_box()
    # an Android keyboard covers roughly the bottom 45% of an 844px screen
    limit = 844 * 0.55
    assert err["y"] + err["height"] <= inp["y"] + 0.5, "error message should be above the input"
    assert btn["y"] + btn["height"] <= limit, "Unlock button would be under the keyboard"


def test_out_of_range_coordinates_in_a_link_are_dropped(app):
    clues = [{"t": "Bad", "lat": 1e9, "lng": -0.12}, {"t": "Good", "lat": 51.5, "lng": -0.12}]
    app.open("#edit=" + b64u(hunt_obj(clues)))
    p = app.page
    expect(p.locator("#editor-panel")).to_be_visible()
    expect(p.locator("#clue-list li")).to_have_count(1)
    expect(p.locator("#clue-list li .txt")).to_have_text("Good")


def test_password_accents_match_however_they_are_typed(app, make_app):
    composed, decomposed = "caf\u00e9", "cafe\u0301"
    player, _ = lock_via_editor(app, composed)
    other = make_app()
    other.open_url(player, wait_fix=False)
    unlock(other, decomposed)
    expect(other.page.locator("#locked")).to_be_hidden()
    expect(other.page.locator("#play-panel")).to_be_visible()


# ============================================================ screenshots
def test_screenshots(app, make_app):
    SHOTS.mkdir(parents=True, exist_ok=True)

    def shot(a, name):
        a.settle(500)
        a.page.screenshot(path=str(SHOTS / name))
        assert (SHOTS / name).stat().st_size > 5000

    app.open("#edit")
    app.settle(400)
    p = app.page
    p.locator("#edit-title").fill("Park treasure hunt")
    p.locator("#edit-password").fill("Golden Acorn")
    for (x, y), text, r in (((120, 200), "Where the old oak keeps watch", 80), ((280, 300), "Behind the bandstand", 20)):
        app.tap_map(x, y)
        p.locator("#edit-clue-text").fill(text)
        app.set_radius(r)
    p.locator("#edit-radius").blur()
    shot(app, "editor.png")
    player, edit = app.get_links()
    shot(app, "share.png")

    b = make_app(start=(north(HOME[0], -200), HOME[1]))
    b.open_url(player)
    expect(b.page.locator("#locked")).to_be_visible()
    shot(b, "locked.png")
    unlock(b, "silver acorn")
    expect(b.page.locator("#locked-err")).to_be_visible()
    shot(b, "locked-error.png")
    unlock(b, "golden acorn")
    expect(b.page.locator("#play-panel")).to_be_visible()
    shot(b, "play.png")

    # walk to the first clue; its position comes from the (plain) twin of this hunt
    p.locator("#btn-share-close").click()
    p.locator("#edit-password").fill("")
    plain_player, _ = app.get_links()
    first = decode_plain(payload_of(plain_player, "hunt"))["clues"][0]
    b.fixes(3, first["lat"], first["lng"])
    expect(b.page.locator("#found")).to_be_visible()
    expect(b.page.locator("#found-clue")).to_have_text("Behind the bandstand")
    shot(b, "found.png")
