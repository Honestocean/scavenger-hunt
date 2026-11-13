"""Fixes from the second review: safer restart, GPS re-arm, editor address bar, build stamp."""
from playwright.sync_api import expect

from test_ui import START, b64u, hunt_obj
from test_puzzles import C1, C2, Q, answer, clue, find_clue, go, only_overlay, play, solve_q


# ----------------------------------------------------------------- restart
def test_restart_button_is_hidden_when_a_skip_code_is_set(app):
    p = play(app, [clue(C1), clue(C2)], skip="skip")
    expect(p.locator("#btn-play-reset")).to_be_hidden()
    expect(p.locator("#btn-stuck")).to_be_visible()


def test_restart_button_stays_without_a_skip_code(app):
    p = play(app, [clue(C1), clue(C2)])
    expect(p.locator("#btn-play-reset")).to_be_visible()


def test_restart_from_the_stuck_screen_needs_the_code(app):
    p = play(app, [clue(C1, Q), clue(C2)], skip="skip")
    solve_q(app)
    go(app)
    find_clue(app, C1["lat"], C1["lng"])
    p.locator("#btn-next").click()
    expect(p.locator("#play-progress")).to_contain_text("2 of 2")
    p.locator("#btn-stuck").click()
    p.locator("#btn-stuck-restart").click()          # no code typed
    expect(p.locator("#stuck-err")).to_be_visible()
    p.locator("#stuck-code").fill("nope")
    p.locator("#btn-stuck-restart").click()
    expect(p.locator("#stuck-err")).to_be_visible()
    app.reload()
    expect(p.locator("#play-progress")).to_contain_text("2 of 2")   # nothing was wiped
    p.locator("#btn-stuck").click()
    p.locator("#stuck-code").fill(" SKIP ")
    p.locator("#btn-stuck-restart").click()
    only_overlay(app, "#puzzle")                     # back at the very start
    expect(p.locator("#puzzle-question")).to_have_text(Q["q"])


# --------------------------------------------------------------- GPS re-arm
COUNT_WATCHES = """() => {
  window.__w = { watch: 0, clear: 0 };
  const g = navigator.geolocation, w = g.watchPosition.bind(g), c = g.clearWatch.bind(g);
  g.watchPosition = (...a) => { window.__w.watch++; return w(...a); };
  g.clearWatch = (...a) => { window.__w.clear++; return c(...a); };
}"""


def test_gps_watch_is_restarted_when_the_page_becomes_visible_again(app):
    p = play(app, [clue(C1), clue(C2)])
    p.evaluate(COUNT_WATCHES)
    p.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    assert p.evaluate("window.__w") == {"watch": 1, "clear": 1}
    find_clue(app, C1["lat"], C1["lng"])             # and arrival still works afterwards
    only_overlay(app, "#found")


def test_gps_watch_is_restarted_when_fixes_stop_arriving(app):
    p = play(app, [clue(C1), clue(C2)])
    app.fix(*START)
    p.evaluate(COUNT_WATCHES)
    p.evaluate("lastFixAt = Date.now() - 45000; checkGpsAlive()")
    assert p.evaluate("window.__w.watch") == 1
    # (the fake GPS answers the new watch at once, so "searching" is replaced straight away)
    app.fix(START[0] + 0.00001, START[1])
    expect(p.locator("#gps-state")).not_to_contain_text("searching")
    find_clue(app, C1["lat"], C1["lng"])
    only_overlay(app, "#found")


def test_gps_watch_is_left_alone_while_fixes_are_fresh(app):
    p = play(app, [clue(C1), clue(C2)])
    app.fix(*START)
    p.evaluate(COUNT_WATCHES)
    p.evaluate("checkGpsAlive()")
    assert p.evaluate("window.__w.watch") == 0


# ------------------------------------------------------------------- editor
def test_get_links_puts_the_edit_link_in_the_address_bar(app):
    app.open("#edit=" + b64u(hunt_obj([clue(C1), clue(C2)])))
    p = app.page
    p.locator("#edit-title").fill("Saved by the address bar")
    p.locator("#edit-password").fill("2015")
    player, edit = app.get_links()
    assert p.evaluate("location.href") == edit
    expect(p.locator("#share")).to_be_visible()      # no reload happened
    app.reload()
    expect(p.locator("#locked")).to_be_hidden()      # this phone already knows the password
    expect(p.locator("#editor-panel")).to_be_visible()
    expect(p.locator("#edit-title")).to_have_value("Saved by the address bar")
    expect(p.locator("#clue-list li")).to_have_count(2)


def test_share_screen_says_how_many_stops(app):
    app.open("#edit=" + b64u(hunt_obj([clue(C1), clue(C2)])))
    app.get_links()
    expect(app.page.locator("#share-note")).to_contain_text("2 stops")


# ---------------------------------------------------------------- page level
def test_pull_to_refresh_is_contained(app):
    app.open("")
    assert app.page.evaluate("getComputedStyle(document.body).overscrollBehaviorY") == "contain"


def test_build_stamp_is_shown(app):
    p = play(app, [clue(C1)])
    expect(p.locator("#build")).to_be_visible()
    assert p.locator("#build").inner_text().strip()
