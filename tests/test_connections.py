"""Connections puzzle (four groups of four) and the organiser's jump code."""
import re

import pytest
from playwright.sync_api import expect

from test_ui import b64u, decode_plain, find_clue, hunt_obj, payload_of
from test_puzzles import C1, C2, Q, clue, dom_text, only_overlay, play

GROUPS = [("Fruit", ["Apple", "Pear", "Plum", "Fig"]),
          ("Colours", ["Red", "Blue", "Green", "Pink"]),
          ("Our songs", ["Beast of Burden", "I'm on Fire", "Loving You", "Rocky Ledge"]),
          ("Vietnam", ["Ha Long Bay", "Hanoi", "Hoi An", "Saigon"])]
CONN = {"k": "c", "g": [{"n": n, "w": w} for n, w in GROUPS]}
C3 = {"t": "Third clue text", "lat": 51.5040, "lng": -0.1200}


def tile(p, word):
    return p.locator("#conn-grid button").filter(has_text=re.compile("^" + re.escape(word) + "$", re.I))


def pick(p, words):
    for w in words:
        tile(p, w).click()


def submit(p):
    p.locator("#btn-conn-submit").click()


def test_board_shows_sixteen_tiles_and_hides_names_and_clue(app):
    p = play(app, [clue(C1, CONN)])
    only_overlay(app, "#puzzle")
    expect(p.locator("#puzzle-c")).to_be_visible()
    expect(p.locator("#puzzle-q")).to_be_hidden()
    expect(p.locator("#puzzle-w")).to_be_hidden()
    expect(p.locator("#conn-grid button")).to_have_count(16)
    for _, words in GROUPS:
        for w in words:
            expect(tile(p, w)).to_have_count(1)
    html = dom_text(app)
    assert C1["t"] not in html
    for name, _ in GROUPS:
        assert name not in html
    expect(p.locator("#btn-conn-submit")).to_be_disabled()


def test_board_fits_a_phone_screen(app):
    p = play(app, [clue(C1, CONN)])
    for b in p.locator("#conn-grid button").all():
        box = b.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390 and box["height"] >= 44
    for sel in ("#btn-conn-submit", "#btn-conn-shuffle", "#btn-conn-clear"):
        box = p.locator(sel).bounding_box()
        assert box["y"] + box["height"] <= 844 and box["x"] >= 0 and box["x"] + box["width"] <= 390, sel


def test_selecting_is_limited_to_four_and_can_be_undone(app):
    p = play(app, [clue(C1, CONN)])
    pick(p, ["Apple", "Pear", "Plum"])
    expect(p.locator("#btn-conn-submit")).to_be_disabled()
    pick(p, ["Red"])
    expect(p.locator("#btn-conn-submit")).to_be_enabled()
    pick(p, ["Blue"])                                   # a fifth is ignored
    expect(p.locator("#conn-grid button.sel")).to_have_count(4)
    pick(p, ["Red"])                                    # tap again to deselect
    expect(p.locator("#conn-grid button.sel")).to_have_count(3)
    p.locator("#btn-conn-clear").click()
    expect(p.locator("#conn-grid button.sel")).to_have_count(0)


def test_wrong_guesses_never_lock_the_player_out(app):
    p = play(app, [clue(C1, CONN)])
    pick(p, ["Apple", "Pear", "Red", "Blue"])
    submit(p)
    expect(p.locator("#conn-msg")).to_contain_text("Not a group")
    p.locator("#btn-conn-clear").click()
    pick(p, ["Apple", "Pear", "Plum", "Red"])
    submit(p)
    expect(p.locator("#conn-msg")).to_contain_text("One away")
    for _ in range(8):
        submit(p)
    expect(p.locator("#conn-grid button")).to_have_count(16)
    expect(p.locator("#conn-solved .grp")).to_have_count(0)
    only_overlay(app, "#puzzle")


def test_a_correct_group_is_revealed_and_survives_a_reload(app):
    p = play(app, [clue(C1, CONN)])
    pick(p, ["Hanoi", "Saigon", "Hoi An", "Ha Long Bay"])
    submit(p)
    expect(p.locator("#conn-solved .grp")).to_have_count(1)
    expect(p.locator("#conn-solved .grp")).to_contain_text("Vietnam")
    expect(p.locator("#conn-solved .grp")).to_contain_text("Ha Long Bay")
    expect(p.locator("#conn-grid button")).to_have_count(12)
    expect(p.locator("#conn-grid button.sel")).to_have_count(0)
    app.reload()
    only_overlay(app, "#puzzle")
    expect(p.locator("#conn-solved .grp")).to_have_count(1)
    expect(p.locator("#conn-grid button")).to_have_count(12)
    assert C1["t"] not in dom_text(app)


def test_solving_all_four_groups_unlocks_the_clue(app):
    p = play(app, [clue(C1, CONN), clue(C2)])
    for _, words in GROUPS:
        pick(p, words)
        submit(p)
    only_overlay(app, "#unlocked")
    expect(p.locator("#unlocked-clue")).to_have_text(C1["t"])
    app.reload()
    only_overlay(app, "#unlocked")
    p.locator("#btn-unlocked-go").click()
    find_clue(app, C1["lat"], C1["lng"])
    only_overlay(app, "#found")


def test_shuffle_keeps_the_same_tiles(app):
    p = play(app, [clue(C1, CONN)])
    before = sorted(p.locator("#conn-grid button").all_inner_texts())
    p.locator("#btn-conn-shuffle").click()
    assert sorted(p.locator("#conn-grid button").all_inner_texts()) == before


def test_skip_code_solves_the_board(app):
    p = play(app, [clue(C1, CONN)], skip="skip")
    p.locator("#btn-stuck-puzzle").click()
    p.locator("#stuck-code").fill("skip")
    p.locator("#btn-stuck-go").click()
    only_overlay(app, "#unlocked")


def test_tile_text_cannot_inject_html(app):
    g = [dict(x) for x in CONN["g"]]
    g[0] = {"n": "<i id=xn>n</i>", "w": ["<b id=xw>x</b>", "Pear", "Plum", "Fig"]}
    p = play(app, [clue(C1, {"k": "c", "g": g})])
    expect(p.locator("#xw")).to_have_count(0)
    pick(p, ["<b id=xw>x</b>", "Pear", "Plum", "Fig"])
    submit(p)
    expect(p.locator("#conn-solved .grp")).to_have_count(1)
    expect(p.locator("#xn")).to_have_count(0)


def _bad():
    three = {"k": "c", "g": CONN["g"][:3]}
    short = {"k": "c", "g": CONN["g"][:3] + [{"n": "x", "w": ["a", "b", "c"]}]}
    dup = {"k": "c", "g": CONN["g"][:3] + [{"n": "x", "w": ["apple", "b", "c", "d"]}]}
    empty = {"k": "c", "g": CONN["g"][:3] + [{"n": "x", "w": ["a", "", "c", "d"]}]}
    typed = {"k": "c", "g": CONN["g"][:3] + [{"n": "x", "w": ["a", 1, "c", "d"]}]}
    return [three, short, dup, empty, typed, {"k": "c"}, {"k": "c", "g": "nope"}, {"k": "c", "g": [None] * 4}]


@pytest.mark.parametrize("bad", _bad())
def test_bad_board_data_means_no_puzzle(app, bad):
    p = play(app, [clue(C1, bad)])
    only_overlay(app, None)
    expect(p.locator("#play-clue")).to_have_text(C1["t"])


def test_saved_state_for_a_board_is_validated(app):
    p = play(app, [clue(C1, CONN)])
    pick(p, GROUPS[0][1])
    submit(p)
    key = p.evaluate("Object.keys(localStorage).find(k => k.startsWith('hunt-puzzle-'))")
    p.evaluate("([k]) => localStorage.setItem(k, JSON.stringify({c: {0: [0, 0, 9, 'x', -1, 2]}}))", [key])
    app.reload()
    expect(p.locator("#conn-solved .grp")).to_have_count(2)      # groups 0 and 2 only
    expect(p.locator("#conn-grid button")).to_have_count(8)


# ------------------------------------------------------------------- editor
LINES = ["Fruit: Apple, Pear, Plum, Fig", "Colours: Red, Blue, Green, Pink",
         "Our songs: Beast of Burden, I'm on Fire, Loving You, Rocky Ledge",
         "Vietnam: Ha Long Bay, Hanoi, Hoi An, Saigon"]


def test_editor_round_trips_a_board(app):
    app.open("#edit=" + b64u(hunt_obj([clue(C1, CONN), clue(C2)])))
    p = app.page
    expect(p.locator("#clue-list li").first).to_contain_text("Connections")
    p.locator("#clue-list li").first.click()
    expect(p.locator("#edit-puzzle-type")).to_have_value("c")
    expect(p.locator("#edit-conn")).to_be_visible()
    assert p.locator("#edit-conn").input_value().splitlines() == LINES
    player, edit = app.get_links()
    d = decode_plain(payload_of(player, "hunt"))
    assert d["clues"][0]["p"] == CONN
    assert decode_plain(payload_of(edit, "edit")) == d


def test_editor_builds_a_board_from_typed_lines(app):
    app.open("#edit=" + b64u(hunt_obj([clue(C1), clue(C2)])))
    p = app.page
    p.locator("#clue-list li").first.click()
    p.locator("#edit-puzzle-type").select_option("c")
    p.locator("#edit-conn").fill("\n".join(LINES) + "\n")
    player, _ = app.get_links()
    assert decode_plain(payload_of(player, "hunt"))["clues"][0]["p"] == CONN


@pytest.mark.parametrize("text", ["\n".join(LINES[:3]), "\n".join(LINES[:3] + ["Odd: a, b, c"]),
                                  "\n".join(LINES[:3] + ["Dup: apple, b, c, d"]), ""])
def test_editor_rejects_an_incomplete_board(app, text):
    app.open("#edit=" + b64u(hunt_obj([clue(C1), clue(C2)])))
    p = app.page
    p.locator("#clue-list li").first.click()
    p.locator("#edit-puzzle-type").select_option("c")
    p.locator("#edit-conn").fill(text)
    p.locator("#btn-share").click()
    expect(p.locator("#share")).to_be_hidden()
    expect(p.locator("#edit-error")).to_be_visible()


# ---------------------------------------------------------------- jump code
def stuck(p, code, from_puzzle=False):
    p.locator("#btn-stuck-puzzle" if from_puzzle else "#btn-stuck").click()
    p.locator("#stuck-code").fill(code)
    p.locator("#btn-stuck-go").click()


def test_jump_code_goes_straight_to_a_later_stop(app):
    p = play(app, [clue(C1, Q), clue(C2), clue(C3, CONN)], skip="skip")
    only_overlay(app, "#puzzle")
    stuck(p, "Skip 3", from_puzzle=True)
    only_overlay(app, "#puzzle")                    # stop 3 starts with its own puzzle
    expect(p.locator("#puzzle-c")).to_be_visible()
    assert C3["t"] not in dom_text(app)
    app.reload()
    only_overlay(app, "#puzzle")
    expect(p.locator("#puzzle-c")).to_be_visible()


def test_jump_code_to_a_stop_without_a_puzzle_shows_its_clue(app):
    p = play(app, [clue(C1), clue(C2), clue(C3)], skip="skip")
    stuck(p, "skip 2")
    only_overlay(app, None)
    expect(p.locator("#play-clue")).to_have_text(C2["t"])
    expect(p.locator("#play-progress")).to_contain_text("2 of 3")
    find_clue(app, C2["lat"], C2["lng"])
    only_overlay(app, "#found")


def test_jump_code_can_go_back_and_clears_later_puzzle_progress(app):
    p = play(app, [clue(C1, CONN), clue(C2), clue(C3)], skip="skip")
    pick(p, GROUPS[0][1])
    submit(p)
    stuck(p, "skip 2", from_puzzle=True)
    expect(p.locator("#play-clue")).to_have_text(C2["t"])
    stuck(p, "skip 1")
    only_overlay(app, "#puzzle")
    expect(p.locator("#conn-grid button")).to_have_count(16)


@pytest.mark.parametrize("code", ["skip 0", "skip 4", "skip x", "skip 2.5", "nope 2", "skip2"])
def test_bad_jump_codes_change_nothing(app, code):
    p = play(app, [clue(C1), clue(C2), clue(C3)], skip="skip")
    stuck(p, code)
    expect(p.locator("#stuck-err")).to_be_visible()
    p.locator("#btn-stuck-cancel").click()
    expect(p.locator("#play-clue")).to_have_text(C1["t"])
