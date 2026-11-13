"""Optional welcome screen shown before the hunt begins."""
import pytest
from playwright.sync_api import expect

from test_ui import START, b64u, decode_plain, find_clue, hunt_obj, payload_of, unlock
from test_puzzles import C1, C2, Q, clue, dom_text, only_overlay, play

ALL = ("#intro", "#puzzle", "#unlocked", "#found", "#finished", "#stuck", "#locked")


def showing(app, sel):
    for o in ALL:
        if o == sel:
            expect(app.page.locator(o)).to_be_visible()
        else:
            expect(app.page.locator(o)).to_be_hidden()


def test_intro_comes_first_explains_and_asks_if_ready(app):
    p = play(app, [clue(C1, Q), clue(C2)], intro=1, title="Our Hunt")
    showing(app, "#intro")
    text = p.locator("#intro").inner_text().lower()
    assert "puzzle" in text and "clue" in text and "ready to begin" in text
    assert "our hunt" in text
    assert C1["t"] not in dom_text(app) and Q["q"] not in p.locator("#intro").inner_text()
    expect(p.locator("#btn-intro-go")).to_be_visible()


def test_intro_lets_the_map_show_through(app):
    p = play(app, [clue(C1, Q)], intro=1)
    showing(app, "#intro")
    bg = p.evaluate("getComputedStyle(document.getElementById('intro')).backgroundColor").replace(" ", "")
    assert bg.startswith("rgba("), bg
    alpha = float(bg.rstrip(")").split(",")[3])
    assert 0.2 <= alpha <= 0.8, bg
    box = p.locator("#btn-intro-go").bounding_box()
    assert box["y"] + box["height"] <= 844 and box["x"] >= 0 and box["x"] + box["width"] <= 390


def test_tapping_ready_starts_the_first_puzzle_and_is_remembered(app):
    p = play(app, [clue(C1, Q), clue(C2)], intro=1)
    p.locator("#btn-intro-go").click()
    showing(app, "#puzzle")
    app.reload()
    showing(app, "#puzzle")


def test_reload_before_starting_shows_the_intro_again(app):
    play(app, [clue(C1, Q)], intro=1)
    app.reload()
    showing(app, "#intro")


def test_no_arrival_while_the_intro_is_up(app):
    p = play(app, [clue(C1), clue(C2)], intro=1, start=(C1["lat"], C1["lng"]))
    find_clue(app, C1["lat"], C1["lng"], n=4)
    showing(app, "#intro")
    p.locator("#btn-intro-go").click()
    showing(app, None)
    expect(p.locator("#play-clue")).to_have_text(C1["t"])
    find_clue(app, C1["lat"], C1["lng"])
    showing(app, "#found")


def test_intro_without_puzzles_does_not_mention_puzzles(app):
    p = play(app, [clue(C1), clue(C2)], intro=1)
    showing(app, "#intro")
    assert "puzzle" not in p.locator("#intro").inner_text().lower()


def test_restart_brings_the_intro_back(app):
    p = play(app, [clue(C1), clue(C2)], intro=1)
    p.locator("#btn-intro-go").click()
    p.locator("#btn-play-reset").click()
    p.locator("#btn-play-reset").click()
    showing(app, "#intro")


@pytest.mark.parametrize("value", [None, 0, False, "", "yes", 2, [1], {"a": 1}])
def test_no_intro_unless_switched_on(app, value):
    extra = {} if value is None else {"intro": value}
    play(app, [clue(C1, Q)], **extra)
    showing(app, "#puzzle")


def test_intro_comes_after_the_password(app, make_app):
    app.open("#edit=" + b64u(hunt_obj([clue(C1, Q)], intro=1)))
    p = app.page
    expect(p.locator("#edit-intro")).to_be_checked()
    p.locator("#edit-password").fill("2015")
    player, edit = app.get_links()
    other = make_app()
    other.open_url(player, wait_fix=False)
    showing(other, "#locked")
    unlock(other, "2015")
    showing(other, "#intro")
    other.page.locator("#btn-intro-go").click()
    showing(other, "#puzzle")


def test_editor_intro_checkbox_round_trips_and_defaults_off(app):
    app.open("#edit=" + b64u(hunt_obj([clue(C1)])))
    p = app.page
    expect(p.locator("#edit-intro")).not_to_be_checked()
    player, _ = app.get_links()
    assert "intro" not in decode_plain(payload_of(player, "hunt"))
    p.locator("#btn-share-close").click()
    p.locator("#edit-intro").check()
    player, edit = app.get_links()
    assert decode_plain(payload_of(player, "hunt"))["intro"] == 1
    app.open_url(edit)
    expect(app.page.locator("#edit-intro")).to_be_checked()
