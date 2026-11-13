"""Headless browser tests for puzzles, the skip code, the final message and
the editor's puzzle / GPS-pin controls. Written from the feature spec.

Reuses the helpers and fixtures of test_ui.py (same server, same fake GPS);
conftest.py makes those fixtures available here.
"""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_ui import (  # the fixtures (app, make_app, ...) come from conftest.py
    HOME, START, b64u, decode_plain, find_clue, hunt_obj, north, payload_of, unlock, visible,
)

SHOTS2 = Path(os.environ.get("SHOTS2_DIR", "/home/claude/work/shots2"))

C1 = {"t": "Alpha clue under the oak", "lat": HOME[0], "lng": HOME[1]}
C2 = {"t": "Bravo clue by the gate", "lat": north(HOME[0], 300), "lng": HOME[1]}
C3 = {"t": "Charlie clue at the quay", "lat": north(HOME[0], 600), "lng": HOME[1]}
Q = {"k": "q", "q": "What has keys but no locks?", "a": "piano|keyboard"}
QH = dict(Q, h="You can play it")
W = {"k": "w", "a": "APPLE"}

OVERLAYS = ("#puzzle", "#unlocked", "#found", "#finished", "#stuck", "#locked")


def clue(base, p=None, **kw):
    c = dict(base, **kw)
    if p is not None:
        c["p"] = p
    return c


def play(app, clues, start=START, **extra):
    app.ctx.set_geolocation({"latitude": start[0], "longitude": start[1], "accuracy": 5})
    app.open("#hunt=" + b64u(hunt_obj(clues, **extra)))
    return app.page


def dom_text(app):
    """Everything a curious player could read out of the page, script source aside."""
    return app.page.evaluate(
        """(() => { const c = document.body.cloneNode(true);
             c.querySelectorAll('script').forEach(s => s.remove());
             const vals = [...document.querySelectorAll('input, textarea')].map(e => e.value).join('\\n');
             return c.innerHTML + '\\n' + vals + '\\n' + document.title; })()""")


def only_overlay(app, sel):
    """Exactly this overlay (or none, for sel=None) is showing."""
    for o in OVERLAYS:
        if o == sel:
            expect(app.page.locator(o)).to_be_visible()
        else:
            expect(app.page.locator(o)).to_be_hidden()


def answer(app, text, enter=False):
    p = app.page
    p.locator("#puzzle-answer").fill(text)
    if enter:
        p.locator("#puzzle-answer").press("Enter")
    else:
        p.locator("#btn-puzzle-submit").click()


def solve_q(app, text="piano"):
    answer(app, text)
    expect(app.page.locator("#unlocked")).to_be_visible()


def go(app):
    app.page.locator("#btn-unlocked-go").click()
    expect(app.page.locator("#unlocked")).to_be_hidden()


def type_word(app, word, enter=True):
    for ch in word.lower():
        app.page.locator(f'#wordle-keys [data-key="{ch}"]').click()
    if enter:
        app.page.locator('#wordle-keys [data-key="enter"]').click()


def tiles(app, row):
    """[(letter, status)] for one grid row; status is hit/near/miss or ''."""
    return [tuple(t) for t in app.page.evaluate(
        """([row]) => { const g = document.getElementById('wordle-grid');
             const n = g.children.length / 6;
             return [...g.children].slice(row * n, row * n + n).map(t => [
               t.textContent.trim().toLowerCase(),
               ['hit', 'near', 'miss'].find(c => t.classList.contains(c)) || '']); }""", [row])]


def statuses(app, row):
    return [s for _, s in tiles(app, row)]


def key_status(app, ch):
    return app.page.evaluate(
        """ch => { const k = document.querySelector('#wordle-keys [data-key="' + ch + '"]');
             return ['hit', 'near', 'miss'].find(c => k.classList.contains(c)) || ''; }""", ch)


# =============================================== A/B. data model + play flow
def test_puzzle_is_the_first_thing_shown_and_hides_the_clue(app):
    p = play(app, [clue(C1, Q), clue(C2)])
    only_overlay(app, "#puzzle")
    expect(p.locator("#puzzle-question")).to_have_text("What has keys but no locks?")
    assert "Alpha" not in dom_text(app) and "under the oak" not in dom_text(app)
    assert "Bravo" not in dom_text(app)
    assert p.evaluate("target") is None


def test_no_arrival_while_the_puzzle_is_unsolved(app):
    p = play(app, [clue(C1, Q), clue(C2)])
    expect(p.locator("#puzzle")).to_be_visible()
    app.fixes(5, C1["lat"], C1["lng"])
    only_overlay(app, "#puzzle")
    assert p.evaluate("target") is None
    # solving afterwards still needs three NEW fixes
    solve_q(app)
    go(app)
    app.fixes(2, C1["lat"], C1["lng"])
    assert not visible(app, "#found")
    app.fix(C1["lat"], C1["lng"])
    expect(p.locator("#found")).to_be_visible()


def test_puzzle_overlay_cannot_be_dismissed(app):
    p = play(app, [clue(C1, Q), clue(C2)])
    expect(p.locator("#puzzle")).to_be_visible()
    p.keyboard.press("Escape")
    labels = [b.inner_text().strip() for b in p.locator("#puzzle button").all() if b.is_visible()]
    assert labels and all(not any(w in l.lower() for w in ("close", "cancel", "skip", "back", "later")) for l in labels), labels
    # the play panel underneath is not reachable
    for sel in ("#btn-play-reset", "#btn-play-center"):
        hit = p.evaluate(
            """sel => { const r = document.querySelector(sel).getBoundingClientRect();
                 const e = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                 return !!e && !!e.closest('#puzzle'); }""", sel)
        assert hit, sel + " is reachable under the puzzle"
    expect(p.locator("#puzzle")).to_be_visible()
    bg = p.evaluate("getComputedStyle(document.getElementById('puzzle')).backgroundColor")
    assert bg.startswith("rgb(") or bg.replace(" ", "").endswith(",1)"), bg


def test_solving_shows_unlocked_then_clue_and_arrival_starts(app):
    p = play(app, [clue(C1, Q), clue(C2)])
    answer(app, "piano")
    only_overlay(app, "#unlocked")
    expect(p.locator("#unlocked")).to_contain_text("Unlocked! Here's your clue:")
    expect(p.locator("#unlocked-clue")).to_have_text(C1["t"])
    # arrival detection has not started while the unlocked card is up
    app.fixes(4, C1["lat"], C1["lng"])
    only_overlay(app, "#unlocked")
    p.locator("#btn-unlocked-go").click()
    only_overlay(app, None)
    expect(p.locator("#play-clue")).to_have_text(C1["t"])
    expect(p.locator("#play-progress")).to_contain_text("1 of 2")
    tgt = p.evaluate("target")
    assert abs(tgt["lat"] - C1["lat"]) < 1e-9
    find_clue(app, C1["lat"], C1["lng"])
    only_overlay(app, "#found")
    expect(p.locator("#found-clue")).to_have_text(C2["t"])  # next clue has no puzzle: as today
    p.locator("#btn-next").click()
    only_overlay(app, None)
    expect(p.locator("#play-clue")).to_have_text(C2["t"])


def test_found_does_not_reveal_a_puzzle_locked_next_clue(app):
    p = play(app, [clue(C1), clue(C2, Q), clue(C3)])
    only_overlay(app, None)
    expect(p.locator("#play-clue")).to_have_text(C1["t"])
    find_clue(app, C1["lat"], C1["lng"])
    only_overlay(app, "#found")
    assert "Bravo" not in dom_text(app) and "by the gate" not in dom_text(app)
    expect(p.locator("#found")).to_contain_text("puzzle")
    assert p.evaluate("target") is None
    p.locator("#btn-next").click()
    only_overlay(app, "#puzzle")
    assert "Bravo" not in dom_text(app)
    expect(p.locator("#puzzle-question")).to_have_text(Q["q"])
    solve_q(app, "Keyboard")
    expect(p.locator("#unlocked-clue")).to_have_text(C2["t"])
    go(app)
    expect(p.locator("#play-clue")).to_have_text(C2["t"])
    expect(p.locator("#play-progress")).to_contain_text("2 of 3")
    find_clue(app, C2["lat"], C2["lng"])
    only_overlay(app, "#found")
    expect(p.locator("#found-clue")).to_have_text(C3["t"])
    expect(p.locator("#found-clue")).to_be_visible()
    p.locator("#btn-next").click()
    find_clue(app, C3["lat"], C3["lng"])
    only_overlay(app, "#finished")


def test_last_clue_with_puzzle_finishes_with_custom_message(app):
    msg = "Turn around… 💍 Will you marry me?"
    p = play(app, [clue(C1, Q)], end=msg)
    solve_q(app)
    go(app)
    find_clue(app, C1["lat"], C1["lng"])
    only_overlay(app, "#finished")
    expect(p.locator("#finished-sub")).to_have_text(msg)
    app.fix(*START)
    app.reload()
    only_overlay(app, "#finished")
    expect(p.locator("#finished-sub")).to_have_text(msg)


def test_finished_default_message_without_end(app):
    p = play(app, [clue(C1)])
    find_clue(app, C1["lat"], C1["lng"])
    expect(p.locator("#finished-sub")).to_have_text("You found every clue. Well done!")


def test_final_message_is_text_not_html(app):
    p = play(app, [clue(C1, dict(Q, q="<b id=xq>Q</b>?", h="<i id=xh>h</i>"), t="<img src=x onerror=window.__x=1> clue")],
             end="<img src=x onerror=window.__x=1><b id=xe>bold</b>", skip="s")
    expect(p.locator("#puzzle-question")).to_have_text("<b id=xq>Q</b>?")
    p.locator("#btn-hint").click()
    expect(p.locator("#puzzle-hint")).to_contain_text("<i id=xh>h</i>")
    solve_q(app)
    expect(p.locator("#unlocked-clue")).to_have_text("<img src=x onerror=window.__x=1> clue")
    go(app)
    find_clue(app, C1["lat"], C1["lng"])
    expect(p.locator("#finished-sub")).to_have_text("<img src=x onerror=window.__x=1><b id=xe>bold</b>")
    assert p.evaluate("window.__x") is None
    assert p.locator("#xq, #xh, #xe").count() == 0


# ------------------------------------------------------------ reload at every stage
def test_reload_with_unsolved_question_keeps_puzzle_and_hint(app):
    p = play(app, [clue(C1, QH), clue(C2)])
    expect(p.locator("#puzzle-hint")).to_be_hidden()
    app.reload()
    only_overlay(app, "#puzzle")
    expect(p.locator("#puzzle-hint")).to_be_hidden()
    p.locator("#btn-hint").click()
    expect(p.locator("#puzzle-hint")).to_be_visible()
    app.reload()
    only_overlay(app, "#puzzle")
    expect(p.locator("#puzzle-hint")).to_be_visible()
    expect(p.locator("#puzzle-hint")).to_contain_text("You can play it")
    assert "Alpha" not in dom_text(app)


def test_reload_after_solving_does_not_ask_again(app):
    p = play(app, [clue(C1, Q), clue(C2)])
    solve_q(app)
    app.reload()  # while the unlocked card is still up
    expect(p.locator("#puzzle")).to_be_hidden()
    expect(p.locator("#unlocked")).to_be_visible()
    expect(p.locator("#unlocked-clue")).to_have_text(C1["t"])
    go(app)
    app.reload()  # solved, card dismissed, not yet arrived
    only_overlay(app, None)
    expect(p.locator("#play-clue")).to_have_text(C1["t"])
    expect(p.locator("#play-progress")).to_contain_text("1 of 2")
    find_clue(app, C1["lat"], C1["lng"])
    only_overlay(app, "#found")


def test_reload_on_found_leads_to_the_next_puzzle(app):
    p = play(app, [clue(C1), clue(C2, Q)])
    find_clue(app, C1["lat"], C1["lng"])
    only_overlay(app, "#found")
    app.fix(*START)
    app.reload()
    expect(p.locator("#finished")).to_be_hidden()
    expect(p.locator("#puzzle")).to_be_visible()  # clue 1 stays found; clue 2's puzzle is next
    assert "Bravo" not in dom_text(app)
    solve_q(app)
    go(app)
    expect(p.locator("#play-progress")).to_contain_text("2 of 2")
    find_clue(app, C2["lat"], C2["lng"])
    only_overlay(app, "#finished")
    app.fix(*START)
    app.reload()
    only_overlay(app, "#finished")


def test_restart_clears_puzzle_progress(app):
    p = play(app, [clue(C1, QH), clue(C2)])
    p.locator("#btn-hint").click()
    solve_q(app)
    go(app)
    btn = p.locator("#btn-play-reset")
    btn.click()
    btn.click()
    only_overlay(app, "#puzzle")
    expect(p.locator("#puzzle-hint")).to_be_hidden()
    assert "Alpha" not in dom_text(app)
    app.reload()
    only_overlay(app, "#puzzle")
    stored = p.evaluate("JSON.stringify(Object.entries(localStorage))")
    assert "hunt-progress" not in stored


def test_puzzle_progress_is_per_hunt(app):
    p = play(app, [clue(C1, Q), clue(C2)])
    solve_q(app)
    go(app)
    p2 = play(app, [clue(C1, Q, t="Other text"), clue(C2)])
    only_overlay(app, "#puzzle")
    assert "Other text" not in dom_text(app)
    # and the first hunt is still where it was
    p3 = play(app, [clue(C1, Q), clue(C2)])
    only_overlay(app, None)
    expect(p3.locator("#play-clue")).to_have_text(C1["t"])


def test_puzzles_work_in_a_password_protected_hunt(app, make_app):
    app.open("#edit=" + b64u(hunt_obj([clue(C1, Q), clue(C2)], end="The end!")))
    expect(app.page.locator("#editor-panel")).to_be_visible()
    app.page.locator("#edit-password").fill("pw")
    player, _ = app.get_links()
    assert payload_of(player, "hunt").startswith("e.")
    b = make_app(start=START)
    b.open_url(player)
    only_overlay(b, "#locked")
    assert "no locks" not in dom_text(b) and "Alpha" not in dom_text(b)
    unlock(b, "pw")
    only_overlay(b, "#puzzle")
    b.reload()
    only_overlay(b, "#puzzle")
    solve_q(b)
    go(b)
    expect(b.page.locator("#play-clue")).to_have_text(C1["t"])


# ------------------------------------------------------------ bad link data
BAD_PUZZLES = [
    "wordle", 7, [], {}, {"k": "x", "a": "apple"}, {"k": "w"}, {"k": "w", "a": "ab"}, {"k": "w", "a": "abcdefghi"},
    {"k": "w", "a": "app1e"}, {"k": "w", "a": "naïve"}, {"k": "w", "a": ["a", "b", "c"]}, {"k": "w", "a": 12345},
    {"k": "w", "a": "two words"},
    {"k": "q"}, {"k": "q", "q": "Question?"}, {"k": "q", "a": "x"}, {"k": "q", "q": "", "a": "x"},
    {"k": "q", "q": "Question?", "a": ""}, {"k": "q", "q": "Question?", "a": " | | "},
    {"k": "q", "q": "Question?", "a": "?!|..."}, {"k": "q", "q": {"x": 1}, "a": "x"},
    {"k": "q", "q": "Question?", "a": ["x"]}, {"k": "q", "q": 5, "a": 5},
]


@pytest.mark.parametrize("bad", BAD_PUZZLES, ids=[str(i) for i in range(len(BAD_PUZZLES))])
def test_invalid_puzzle_data_degrades_to_no_puzzle(app, bad):
    p = play(app, [clue(C1, bad), clue(C2)], skip=["x"], end={"a": 1})
    only_overlay(app, None)
    expect(p.locator("#play-clue")).to_have_text(C1["t"])
    expect(p.locator("#btn-stuck")).to_be_hidden()
    find_clue(app, C1["lat"], C1["lng"])
    only_overlay(app, "#found")
    expect(p.locator("#found-clue")).to_have_text(C2["t"])
    p.locator("#btn-next").click()
    find_clue(app, C2["lat"], C2["lng"])
    only_overlay(app, "#finished")
    expect(p.locator("#finished-sub")).to_have_text("You found every clue. Well done!")


def test_link_fields_are_length_limited_and_tolerant(app):
    p = play(app, [clue(C1, {"k": "q", "q": "Q" * 2000, "a": "yes", "h": 42, "junk": {"x": 1}})], end="E" * 5000)
    only_overlay(app, "#puzzle")
    assert len(p.locator("#puzzle-question").inner_text()) <= 500
    expect(p.locator("#btn-hint")).to_be_hidden()  # a non-string hint is no hint
    solve_q(app, "yes")
    go(app)
    find_clue(app, C1["lat"], C1["lng"])
    assert len(p.locator("#finished-sub").inner_text()) == 300


def test_wordle_word_in_link_may_be_lower_case(app):
    p = play(app, [clue(C1, {"k": "w", "a": "apple"})])
    only_overlay(app, "#puzzle")
    type_word(app, "apple")
    expect(p.locator("#unlocked")).to_be_visible()


def test_corrupt_saved_state_does_not_break_play(app):
    p = play(app, [clue(C1, W), clue(C2, Q)])
    expect(p.locator("#puzzle")).to_be_visible()
    keys = p.evaluate("Object.keys(localStorage)")
    for junk in ('{"w":{"0":{"g":["toolongguess",5,null,"ab<de"],"k":"zz"}},"s":7,"h":"x","u":[]}', "not json", "null", "[1,2]",
                 '{"w":{"0":{"g":["zzzzz","zzzzz","zzzzz","zzzzz","zzzzz","zzzzz","zzzzz","zzzzz"]}}}'):
        p.evaluate("([ks, v]) => { for (const k of Object.keys(localStorage)) localStorage.setItem(k, v);"
                   " for (const k of ks) localStorage.setItem(k, v); }", [keys, junk])
        app.reload()
        only_overlay(app, "#puzzle")
        expect(p.locator("#wordle-grid")).to_be_visible()
    p.locator("#btn-wordle-retry").click() if visible(app, "#btn-wordle-retry") else None
    type_word(app, "apple")
    expect(p.locator("#unlocked")).to_be_visible()


# ======================================================= D. question puzzle
@pytest.mark.parametrize("typed", [
    "piano", "PIANO", "  Piano  ", "the piano", "A Piano", "an  PIANO!", "Piano.", "“piano”", "p-i-a-n-o"[0:1] + "iano?!",
    "ｐｉａｎｏ", "keyboard", "The   Keyboard",
])
def test_question_accepts_forgiving_answers(app, typed):
    p = play(app, [clue(C1, Q), clue(C2)])
    answer(app, typed)
    expect(p.locator("#unlocked")).to_be_visible()


@pytest.mark.parametrize("stored,typed", [
    ("St. David's Head", "st davids head"), ("st davids head", "St. David's  Head!"),
    ("The Golden Lion", "golden lion"), ("golden lion", "the Golden Lion"),
    ("ice   cream", " Ice Cream "), ("café", "café"), ("An apple|A pear", "pear"),
    (" seven | 7 ", "7"), ("the", "the"), ("a", "A"),
])
def test_question_answer_normalisation(app, stored, typed):
    p = play(app, [clue(C1, dict(Q, a=stored)), clue(C2)])
    answer(app, typed, enter=True)
    expect(p.locator("#unlocked")).to_be_visible()


@pytest.mark.parametrize("typed", ["", "   ", "pian", "pianos", "piano keyboard", "piano|keyboard", "|", "the", "?!"])
def test_question_wrong_answer_says_so_and_lets_you_retry(app, typed):
    p = play(app, [clue(C1, Q), clue(C2)])
    expect(p.locator("#puzzle-wrong")).to_be_hidden()
    answer(app, typed)
    app.settle(250)
    only_overlay(app, "#puzzle")
    if typed.strip():
        expect(p.locator("#puzzle-wrong")).to_be_visible()
        expect(p.locator("#puzzle-wrong")).to_contain_text("Not quite — try again")
    expect(p.locator("#puzzle-answer")).to_be_editable()
    assert "Alpha" not in dom_text(app)
    answer(app, "piano", enter=True)
    expect(p.locator("#unlocked")).to_be_visible()


def test_question_hint_button_only_when_a_hint_is_set(app):
    p = play(app, [clue(C1, Q), clue(C2, QH)])
    expect(p.locator("#puzzle")).to_be_visible()
    expect(p.locator("#btn-hint")).to_be_hidden()
    expect(p.locator("#puzzle-hint")).to_be_hidden()
    solve_q(app)
    go(app)
    find_clue(app, C1["lat"], C1["lng"])
    p.locator("#btn-next").click()
    only_overlay(app, "#puzzle")
    expect(p.locator("#puzzle-wrong")).to_be_hidden()
    expect(p.locator("#puzzle-answer")).to_have_value("")
    expect(p.locator("#btn-hint")).to_be_visible()
    expect(p.locator("#puzzle-hint")).to_be_hidden()
    assert "You can play it" not in p.locator("body").inner_text()
    p.locator("#btn-hint").click()
    expect(p.locator("#puzzle-hint")).to_be_visible()
    expect(p.locator("#puzzle-hint")).to_contain_text("You can play it")


def test_question_layout_clears_the_android_keyboard(app):
    long_q = "I speak without a mouth and hear without ears. I have no body, but I come alive with wind. What am I?"
    p = play(app, [clue(C1, dict(QH, q=long_q)), clue(C2)], skip="x")
    p.locator("#btn-hint").click()
    answer(app, "nope")
    expect(p.locator("#puzzle-wrong")).to_be_visible()
    wrong = p.locator("#puzzle-wrong").bounding_box()
    inp = p.locator("#puzzle-answer").bounding_box()
    btn = p.locator("#btn-puzzle-submit").bounding_box()
    limit = 844 * 0.55
    assert wrong["y"] >= 0 and inp["y"] >= 0
    assert wrong["y"] + wrong["height"] <= inp["y"] + 0.5, "message should be above the input"
    for name, box in (("wrong", wrong), ("input", inp), ("submit", btn)):
        assert box["y"] + box["height"] <= limit, f"{name} would be under the keyboard ({box})"
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert inp["height"] >= 44 and btn["height"] >= 44
    a = p.locator("#puzzle-answer")
    assert a.get_attribute("autocomplete") == "off"
    assert a.get_attribute("autocapitalize") == "none"
    assert a.get_attribute("autocorrect") == "off"
    assert a.get_attribute("spellcheck") == "false"
    assert float(p.evaluate("getComputedStyle(document.getElementById('puzzle-answer')).fontSize")[:-2]) >= 16


# ================================================================ C. Wordle
def wordle(app, word="APPLE", **extra):
    p = play(app, [clue(C1, {"k": "w", "a": word}), clue(C2)], **extra)
    only_overlay(app, "#puzzle")
    expect(p.locator("#wordle-grid")).to_be_visible()
    return p


def test_wordle_grid_and_keyboard_structure(app):
    p = wordle(app, "APPLE")
    assert p.locator("#wordle-grid > *").count() == 6 * 5
    keys = p.evaluate("[...document.querySelectorAll('#wordle-keys [data-key]')].map(k => [k.tagName, k.dataset.key])")
    assert all(tag == "BUTTON" for tag, _ in keys)
    assert sorted(k for _, k in keys) == sorted(list("abcdefghijklmnopqrstuvwxyz") + ["enter", "back"])
    expect(p.locator("#puzzle-q")).to_be_hidden()
    expect(p.locator("#btn-wordle-retry")).to_be_hidden()
    assert all(t == ("", "") for r in range(6) for t in tiles(app, r))
    assert "apple" not in dom_text(app).lower(), "the answer must not be sitting in the DOM"


@pytest.mark.parametrize("n", [3, 6, 8])
def test_wordle_grid_matches_word_length(app, n):
    p = wordle(app, "ABCDEFGH"[:n])
    assert p.locator("#wordle-grid > *").count() == 6 * n
    boxes = p.evaluate("[...document.getElementById('wordle-grid').children].map(t => { const r = t.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; })")
    assert len({round(b[1]) for b in boxes}) == 6, "six rows"
    assert len({round(b[0]) for b in boxes}) == n, f"{n} columns"
    assert all(b[0] >= 0 and b[0] + b[2] <= 390 and b[2] >= 28 for b in boxes)


@pytest.mark.parametrize("word,guess,expected", [
    ("apple", "puppy", ["near", "miss", "hit", "miss", "miss"]),
    ("puppy", "apple", ["miss", "near", "hit", "miss", "miss"]),
    ("robot", "floor", ["miss", "miss", "near", "hit", "near"]),
    ("abbey", "kebab", ["miss", "near", "hit", "near", "near"]),
    ("level", "geese", ["miss", "hit", "near", "miss", "miss"]),
    ("geese", "level", ["miss", "hit", "miss", "near", "miss"]),
    ("crane", "nacre", ["near", "near", "near", "near", "hit"]),
    ("sea", "ass", ["near", "near", "miss"]),
    ("aaa", "aba", ["hit", "miss", "hit"]),
])
def test_wordle_scoring_with_duplicates(app, word, guess, expected):
    wordle(app, word.upper())
    type_word(app, guess)
    assert tiles(app, 0) == list(zip(guess, expected))
    assert all(t == ("", "") for t in tiles(app, 1))


def test_wordle_keyboard_keeps_best_status(app):
    wordle(app, "APPLE")
    type_word(app, "puppy")
    assert key_status(app, "p") == "hit"  # hit beats near and miss from the same guess
    assert key_status(app, "u") == "miss" and key_status(app, "y") == "miss"
    assert key_status(app, "a") == "" and key_status(app, "enter") == ""
    type_word(app, "lemon")
    assert key_status(app, "l") == "near" and key_status(app, "e") == "near"
    type_word(app, "eagle")  # e: near, then hit at the end; l: now a hit
    assert key_status(app, "e") == "hit" and key_status(app, "l") == "hit" and key_status(app, "a") == "near"
    type_word(app, "leave")  # l near here, but it stays the best seen: hit
    assert statuses(app, 3)[0] == "near"
    assert key_status(app, "l") == "hit" and key_status(app, "p") == "hit" and key_status(app, "u") == "miss"


def test_wordle_typing_back_and_short_guess(app):
    p = wordle(app, "APPLE")
    type_word(app, "pup", enter=False)
    assert tiles(app, 0) == [("p", ""), ("u", ""), ("p", ""), ("", ""), ("", "")]
    p.locator('#wordle-keys [data-key="back"]').click()
    assert [l for l, _ in tiles(app, 0)] == ["p", "u", "", "", ""]
    expect(p.locator("#wordle-msg")).to_have_text("")
    p.locator('#wordle-keys [data-key="enter"]').click()  # too short
    assert p.locator("#wordle-msg").inner_text().strip()
    expect(p.locator("#wordle-msg")).to_be_visible()
    assert tiles(app, 0) == [("p", ""), ("u", ""), ("", ""), ("", ""), ("", "")], "a short guess must not use up a row"
    assert key_status(app, "p") == ""
    type_word(app, "ppyzzz", enter=False)  # extra letters are ignored
    assert [l for l, _ in tiles(app, 0)] == list("puppy")
    assert all(t == ("", "") for t in tiles(app, 1))
    p.locator('#wordle-keys [data-key="enter"]').click()
    assert statuses(app, 0) == ["near", "miss", "hit", "miss", "miss"]
    expect(p.locator("#wordle-msg")).to_have_text("")
    for _ in range(3):  # back on an empty row does nothing, and never edits a played row
        p.locator('#wordle-keys [data-key="back"]').click()
    assert [l for l, _ in tiles(app, 0)] == list("puppy")
    p.locator('#wordle-keys [data-key="enter"]').click()  # empty row
    assert all(t == ("", "") for t in tiles(app, 1))


def test_wordle_has_no_dictionary_check(app):
    wordle(app, "APPLE")
    type_word(app, "zzzzz")
    assert tiles(app, 0) == [("z", "miss")] * 5
    type_word(app, "qxjkv")
    assert statuses(app, 1) == ["miss"] * 5


def test_wordle_correct_guess_unlocks_the_clue(app):
    p = wordle(app, "APPLE")
    type_word(app, "maple")
    only_overlay(app, "#puzzle")
    assert "Alpha" not in dom_text(app)
    type_word(app, "apple")
    assert statuses(app, 1) == ["hit"] * 5
    only_overlay(app, "#unlocked")
    expect(p.locator("#unlocked-clue")).to_have_text(C1["t"])
    app.reload()
    only_overlay(app, "#unlocked")
    go(app)
    expect(p.locator("#play-clue")).to_have_text(C1["t"])
    find_clue(app, C1["lat"], C1["lng"])
    only_overlay(app, "#found")


def test_wordle_correct_on_the_sixth_guess_wins(app):
    wordle(app, "APPLE")
    for _ in range(5):
        type_word(app, "zzzzz")
    expect(app.page.locator("#btn-wordle-retry")).to_be_hidden()
    type_word(app, "apple")
    only_overlay(app, "#unlocked")


def test_wordle_physical_keyboard(app):
    p = wordle(app, "APPLE")
    for k in ("p", "U", "x", "Backspace", "p", "p", "y", "1", " ", "Enter"):
        p.keyboard.press(k)
    assert tiles(app, 0) == list(zip("puppy", ["near", "miss", "hit", "miss", "miss"]))
    p.keyboard.press("Control+a")  # shortcuts are not letters
    assert all(t == ("", "") for t in tiles(app, 1))
    for k in "apple":
        p.keyboard.press(k)
    p.keyboard.press("Enter")
    only_overlay(app, "#unlocked")
    # keys typed after solving do nothing harmful
    p.keyboard.press("z")
    p.keyboard.press("Enter")
    only_overlay(app, "#unlocked")


def test_wordle_six_wrong_guesses_offer_unlimited_retries(app):
    p = wordle(app, "APPLE")
    for g in ("puppy", "lemon", "zzzzz", "qqqqq", "eagle", "leave"):
        expect(p.locator("#btn-wordle-retry")).to_be_hidden()
        type_word(app, g)
    only_overlay(app, "#puzzle")
    expect(p.locator("#btn-wordle-retry")).to_be_visible()
    assert p.locator("#wordle-msg").inner_text().strip()
    assert "apple" not in dom_text(app).lower(), "the answer is not given away"
    assert [l for l, _ in tiles(app, 5)] == list("leave")
    type_word(app, "apple")  # no seventh row: typing does nothing until "retry"
    only_overlay(app, "#puzzle")
    assert [l for l, _ in tiles(app, 5)] == list("leave")
    p.locator("#btn-wordle-retry").click()
    expect(p.locator("#btn-wordle-retry")).to_be_hidden()
    assert all(t == ("", "") for r in range(6) for t in tiles(app, r)), "grid is cleared"
    assert key_status(app, "p") == "hit" and key_status(app, "z") == "miss" and key_status(app, "a") == "near"
    # a second failed round, then success on a third
    for _ in range(6):
        type_word(app, "wrong")
    expect(p.locator("#btn-wordle-retry")).to_be_visible()
    p.locator("#btn-wordle-retry").click()
    assert all(t == ("", "") for r in range(6) for t in tiles(app, r))
    type_word(app, "apple")
    only_overlay(app, "#unlocked")


def test_wordle_reload_keeps_guesses_and_key_colours(app):
    p = wordle(app, "APPLE")
    type_word(app, "puppy")
    type_word(app, "lemon")
    type_word(app, "ea", enter=False)  # letters not yet entered are not kept
    app.reload()
    only_overlay(app, "#puzzle")
    assert tiles(app, 0) == list(zip("puppy", ["near", "miss", "hit", "miss", "miss"]))
    assert tiles(app, 1) == list(zip("lemon", ["near", "near", "miss", "miss", "miss"]))
    assert all(t == ("", "") for r in range(2, 6) for t in tiles(app, r))
    assert key_status(app, "p") == "hit" and key_status(app, "u") == "miss" and key_status(app, "l") == "near"
    assert "Alpha" not in dom_text(app)
    type_word(app, "eagle")
    assert [l for l, _ in tiles(app, 2)] == list("eagle")
    type_word(app, "apple")
    only_overlay(app, "#unlocked")


def test_wordle_reload_after_failing_and_after_retry(app):
    p = wordle(app, "APPLE")
    for g in ("puppy", "zzzzz", "zzzzz", "zzzzz", "zzzzz", "lemon"):
        type_word(app, g)
    expect(p.locator("#btn-wordle-retry")).to_be_visible()
    app.reload()
    only_overlay(app, "#puzzle")
    expect(p.locator("#btn-wordle-retry")).to_be_visible()
    assert [l for l, _ in tiles(app, 5)] == list("lemon")
    p.locator("#btn-wordle-retry").click()
    type_word(app, "maple")
    app.reload()
    only_overlay(app, "#puzzle")
    expect(p.locator("#btn-wordle-retry")).to_be_hidden()
    assert [l for l, _ in tiles(app, 0)] == list("maple")
    assert all(t == ("", "") for r in range(1, 6) for t in tiles(app, r))
    assert key_status(app, "u") == "miss" and key_status(app, "z") == "miss" and key_status(app, "p") == "hit"


def test_wordle_restart_clears_guesses(app):
    p = play(app, [clue(C1), clue(C2, W)])
    find_clue(app, C1["lat"], C1["lng"])
    p.locator("#btn-next").click()
    only_overlay(app, "#puzzle")
    type_word(app, "puppy")
    type_word(app, "apple")
    go(app)
    btn = p.locator("#btn-play-reset")
    btn.click()
    btn.click()
    expect(p.locator("#play-clue")).to_have_text(C1["t"])
    find_clue(app, C1["lat"], C1["lng"])
    p.locator("#btn-next").click()
    only_overlay(app, "#puzzle")
    assert all(t == ("", "") for r in range(6) for t in tiles(app, r))
    assert key_status(app, "p") == ""


def test_two_wordles_do_not_share_state(app):
    p = play(app, [clue(C1, W), clue(C2, {"k": "w", "a": "GRAPES"})])
    type_word(app, "puppy")
    type_word(app, "apple")
    go(app)
    find_clue(app, C1["lat"], C1["lng"])
    expect(p.locator("#found")).to_contain_text("puzzle")
    p.locator("#btn-next").click()
    only_overlay(app, "#puzzle")
    assert p.locator("#wordle-grid > *").count() == 36
    assert all(t == ("", "") for r in range(6) for t in tiles(app, r))
    assert all(key_status(app, ch) == "" for ch in "puyale")
    expect(p.locator("#wordle-msg")).to_have_text("")
    type_word(app, "grapes")
    expect(p.locator("#unlocked-clue")).to_have_text(C2["t"])


@pytest.mark.parametrize("word", ["CAT", "APPLE", "PLANET", "ABCDEFGH"])
def test_wordle_fits_the_screen_without_scrolling(app, word):
    p = wordle(app, word, skip="letmein")
    wrong = "z" * len(word)
    for _ in range(6):
        type_word(app, wrong)
    expect(p.locator("#btn-wordle-retry")).to_be_visible()  # the tallest state
    expect(p.locator("#btn-stuck-puzzle")).to_be_visible()
    m = p.evaluate("(() => { const e = document.getElementById('puzzle'); return [e.scrollHeight, e.clientHeight, e.scrollWidth, e.clientWidth, document.documentElement.scrollHeight, innerHeight]; })()")
    assert m[0] <= m[1] and m[2] <= m[3], f"puzzle overlay scrolls: {m}"
    assert m[4] <= m[5], f"page scrolls: {m}"
    for sel in ("#wordle-grid", "#wordle-msg", "#btn-wordle-retry", "#wordle-keys", "#btn-stuck-puzzle"):
        b = p.locator(sel).bounding_box()
        assert b["y"] >= 0 and b["y"] + b["height"] <= 844 and b["x"] >= 0 and b["x"] + b["width"] <= 390, (sel, b)
    keys = p.evaluate("[...document.querySelectorAll('#wordle-keys [data-key]')].map(k => { const r = k.getBoundingClientRect(); return [k.dataset.key, r.x, r.y, r.width, r.height]; })")
    assert len(keys) == 28
    for name, x, y, w, h in keys:
        assert w >= 30 and h >= 44, f"key {name} is too small to tap: {w}x{h}"
        assert x >= 0 and x + w <= 390 and y >= 0 and y + h <= 844, name
    # no two keys overlap
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            assert a[1] + a[3] <= b[1] + 0.5 or b[1] + b[3] <= a[1] + 0.5 or a[2] + a[4] <= b[2] + 0.5 or b[2] + b[4] <= a[2] + 0.5, (a, b)
    # each key is really tappable (nothing lies on top of it)
    for name, x, y, w, h in keys:
        hit = p.evaluate("([x, y, k]) => { const e = document.elementFromPoint(x, y); return !!e && !!e.closest('[data-key=\"' + k + '\"]'); }",
                         [x + w / 2, y + h / 2, name])
        assert hit, name


# ============================================================= E. skip code
def use_code(app, code, opener="#btn-stuck", enter=False):
    p = app.page
    p.locator(opener).click()
    expect(p.locator("#stuck")).to_be_visible()
    p.locator("#stuck-code").fill(code)
    if enter:
        p.locator("#stuck-code").press("Enter")
    else:
        p.locator("#btn-stuck-go").click()


def test_stuck_controls_hidden_without_a_skip_code(app):
    p = play(app, [clue(C1, Q), clue(C2)])
    only_overlay(app, "#puzzle")
    expect(p.locator("#btn-stuck-puzzle")).to_be_hidden()
    solve_q(app)
    go(app)
    expect(p.locator("#btn-stuck")).to_be_hidden()
    expect(p.locator("#btn-play-reset")).to_be_visible()


@pytest.mark.parametrize("skip", ["", "   ", None, 5])
def test_blank_or_bad_skip_code_means_no_skip(app, skip):
    p = play(app, [clue(C1), clue(C2)], skip=skip)
    expect(p.locator("#play-panel")).to_be_visible()
    expect(p.locator("#btn-stuck")).to_be_hidden()


def test_skip_code_solves_the_showing_puzzle(app):
    p = play(app, [clue(C1, Q), clue(C2)], skip="Rescue Me")
    only_overlay(app, "#puzzle")
    expect(p.locator("#btn-stuck-puzzle")).to_be_visible()
    p.locator("#btn-stuck-puzzle").click()
    expect(p.locator("#stuck")).to_be_visible()
    expect(p.locator("#stuck-err")).to_be_hidden()
    assert "Rescue" not in dom_text(app)
    # the stuck screen is on top of the puzzle
    top = p.evaluate("(() => { const e = document.elementFromPoint(195, 400); return !!e.closest('#stuck'); })()")
    assert top
    p.locator("#stuck-code").fill("wrong")
    p.locator("#btn-stuck-go").click()
    expect(p.locator("#stuck-err")).to_be_visible()
    expect(p.locator("#stuck")).to_be_visible()
    assert "Alpha" not in dom_text(app)
    p.locator("#stuck-code").fill("  rescue me ")
    p.locator("#btn-stuck-go").click()
    only_overlay(app, "#unlocked")  # flow continues exactly as if solved
    expect(p.locator("#unlocked-clue")).to_have_text(C1["t"])
    app.reload()
    only_overlay(app, "#unlocked")
    go(app)
    expect(p.locator("#play-clue")).to_have_text(C1["t"])
    find_clue(app, C1["lat"], C1["lng"])
    only_overlay(app, "#found")


def test_wrong_skip_code_changes_nothing_and_cancel_returns(app):
    p = play(app, [clue(C1, W), clue(C2)], skip="rescue")
    type_word(app, "puppy")
    use_code(app, "nope", "#btn-stuck-puzzle", enter=True)
    expect(p.locator("#stuck-err")).to_be_visible()
    expect(p.locator("#stuck")).to_be_visible()
    # typing in the code box must not leak into the Wordle underneath
    p.locator("#stuck-code").press("z")
    p.locator("#stuck-code").press("Backspace")
    p.locator("#btn-stuck-cancel").click()
    only_overlay(app, "#puzzle")
    assert [l for l, _ in tiles(app, 0)] == list("puppy")
    assert all(t == ("", "") for t in tiles(app, 1))
    app.reload()
    only_overlay(app, "#puzzle")
    assert [l for l, _ in tiles(app, 0)] == list("puppy")
    # reopening starts clean
    p.locator("#btn-stuck-puzzle").click()
    expect(p.locator("#stuck-err")).to_be_hidden()
    expect(p.locator("#stuck-code")).to_have_value("")
    p.locator("#btn-stuck-cancel").click()
    type_word(app, "apple")
    only_overlay(app, "#unlocked")


def test_skip_code_solves_a_wordle(app):
    p = play(app, [clue(C1, W), clue(C2)], skip="rescue")
    type_word(app, "puppy")
    use_code(app, "RESCUE", "#btn-stuck-puzzle", enter=True)
    only_overlay(app, "#unlocked")
    expect(p.locator("#unlocked-clue")).to_have_text(C1["t"])


def test_skip_code_marks_the_current_location_found(app):
    p = play(app, [clue(C1), clue(C2, Q), clue(C3)], skip="rescue", end="Fin!")
    only_overlay(app, None)
    expect(p.locator("#btn-stuck")).to_be_visible()
    use_code(app, "bad")
    expect(p.locator("#stuck-err")).to_be_visible()
    p.locator("#btn-stuck-cancel").click()
    only_overlay(app, None)
    expect(p.locator("#play-clue")).to_have_text(C1["t"])
    expect(p.locator("#play-progress")).to_contain_text("1 of 3")

    use_code(app, "Rescue")  # far from clue 1, but it counts as found
    only_overlay(app, "#found")
    expect(p.locator("#found")).to_contain_text("puzzle")
    assert "Bravo" not in dom_text(app)
    app.reload()
    only_overlay(app, "#puzzle")
    solve_q(app)
    go(app)
    expect(p.locator("#play-progress")).to_contain_text("2 of 3")
    use_code(app, "rescue", enter=True)
    only_overlay(app, "#found")
    expect(p.locator("#found-clue")).to_have_text(C3["t"])
    p.locator("#btn-next").click()
    only_overlay(app, None)
    expect(p.locator("#play-clue")).to_have_text(C3["t"])
    use_code(app, "rescue")  # last clue: straight to the finish
    only_overlay(app, "#finished")
    expect(p.locator("#finished-sub")).to_have_text("Fin!")
    app.reload()
    only_overlay(app, "#finished")


def test_skip_then_gps_does_not_double_count(app):
    """Standing on clue 1 when the code is used must not also fire a GPS arrival for it."""
    p = play(app, [clue(C1), clue(C2), clue(C3)], start=(C1["lat"], C1["lng"]), skip="rescue")
    app.fix(C1["lat"], C1["lng"])  # with the fix from opening the page: two of the three needed
    only_overlay(app, None)
    use_code(app, "rescue")
    only_overlay(app, "#found")
    app.fixes(4, C1["lat"], C1["lng"])
    only_overlay(app, "#found")
    expect(p.locator("#found-clue")).to_have_text(C2["t"])
    p.locator("#btn-next").click()
    app.fixes(4, C1["lat"], C1["lng"])
    only_overlay(app, None)
    expect(p.locator("#play-progress")).to_contain_text("2 of 3")


def test_stuck_screen_layout_and_input_attributes(app):
    p = play(app, [clue(C1), clue(C2)], skip="rescue")
    use_code(app, "nope")
    expect(p.locator("#stuck-err")).to_be_visible()
    limit = 844 * 0.55
    for sel in ("#stuck-err", "#stuck-code", "#btn-stuck-go"):
        b = p.locator(sel).bounding_box()
        assert b["y"] >= 0 and b["y"] + b["height"] <= limit, sel
    c = p.locator("#stuck-code")
    assert c.get_attribute("autocapitalize") == "none" and c.get_attribute("autocomplete") == "off"
    expect(p.locator("#btn-stuck-cancel")).to_be_visible()
    # the play-panel control is small but tappable and does not push the other buttons off screen
    p.locator("#btn-stuck-cancel").click()
    for sel in ("#btn-play-center", "#btn-play-reset", "#btn-stuck"):
        b = p.locator(sel).bounding_box()
        assert b["x"] >= 0 and b["x"] + b["width"] <= 390 and b["y"] + b["height"] <= 844 and b["height"] >= 40, (sel, b)


# ================================================================ F. editor
SPOTS = [(110, 190), (280, 300), (140, 330), (300, 180)]


def editor(app, n=0):
    """Open a blank editor and drop n pins by tapping the map."""
    app.open("#edit")
    app.settle(400)
    for i in range(n):
        app.tap_map(*SPOTS[i])
        app.page.locator("#edit-clue-text").fill(f"Clue text {i + 1}")
    return app.page


def select_row(app, i):
    app.page.locator("#clue-list li").nth(i).click()
    assert selected_row(app) == i


def selected_row(app):
    return app.page.evaluate(
        "[...document.querySelectorAll('#clue-list li')].findIndex(li => li.classList.contains('selected'))")


def set_wordle(app, i, word):
    select_row(app, i)
    app.page.locator("#edit-puzzle-type").select_option("w")
    app.page.locator("#edit-wordle-word").fill(word)


def set_question(app, i, q, a, h=""):
    select_row(app, i)
    p = app.page
    p.locator("#edit-puzzle-type").select_option("q")
    p.locator("#edit-q-text").fill(q)
    p.locator("#edit-q-answers").fill(a)
    p.locator("#edit-q-hint").fill(h)


def share_fails(app):
    p = app.page
    p.locator("#btn-share").click()
    expect(p.locator("#edit-error")).to_be_visible()
    app.settle(300)
    expect(p.locator("#share")).to_be_hidden()
    assert p.locator("#edit-error").inner_text().strip()


def markers(app):
    return [tuple(m) for m in app.page.evaluate(
        """(() => { const out = []; map.eachLayer(l => { if (l instanceof L.Marker) {
             const p = l.getLatLng(); out.push([p.lat, p.lng]); } }); return out; })()""")]


def near(a, b, tol=2e-6):
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def test_editor_has_hunt_level_skip_and_end_fields(app):
    p = editor(app, 1)
    for sel in ("#edit-skip", "#edit-end", "#btn-pin-here", "#btn-move-here"):
        assert p.evaluate("sel => !!document.querySelector(sel).closest('#editor-scroll')", sel), sel
    assert p.locator("#edit-end").get_attribute("maxlength") == "300"
    p.locator("#edit-skip").fill("  Rescue Me ")
    p.locator("#edit-end").fill("Turn around… 💍")
    player, edit = app.get_links()
    for d in (decode_plain(payload_of(player, "hunt")), decode_plain(payload_of(edit, "edit"))):
        assert d["skip"] == "Rescue Me"
        assert d["end"] == "Turn around… 💍"
        assert "p" not in d["clues"][0]


def test_editor_blank_skip_and_end_are_left_out_of_the_link(app):
    p = editor(app, 1)
    p.locator("#edit-skip").fill("   ")
    player, _ = app.get_links()
    d = decode_plain(payload_of(player, "hunt"))
    assert "skip" not in d and "end" not in d


def test_editor_puzzle_fields_follow_the_type(app):
    p = editor(app, 1)
    sel = p.locator("#edit-puzzle-type")
    expect(sel).to_be_visible()
    assert p.evaluate("[...document.getElementById('edit-puzzle-type').options].map(o => o.value)") == ["none", "w", "q"]
    expect(sel).to_have_value("none")
    q_fields = ("#edit-q-text", "#edit-q-answers", "#edit-q-hint")
    for f in ("#edit-wordle-word",) + q_fields:
        expect(p.locator(f)).to_be_hidden()
    sel.select_option("w")
    expect(p.locator("#edit-wordle-word")).to_be_visible()
    for f in q_fields:
        expect(p.locator(f)).to_be_hidden()
    sel.select_option("q")
    expect(p.locator("#edit-wordle-word")).to_be_hidden()
    for f in q_fields:
        expect(p.locator(f)).to_be_visible()
    sel.select_option("none")
    for f in ("#edit-wordle-word",) + q_fields:
        expect(p.locator(f)).to_be_hidden()
    for f in ("#edit-puzzle-type", "#edit-wordle-word") + q_fields:
        assert p.evaluate("sel => !!document.querySelector(sel).closest('#editor-scroll')", f), f
    text = p.locator("#clue-text-wrap").inner_text().lower()
    assert "puzzle first" in text and "clue text" in text and "pin" in text, text


def test_editor_puzzle_fields_are_per_clue(app):
    p = editor(app, 3)
    set_wordle(app, 0, "apple")
    set_question(app, 1, "What has keys?", "piano|keyboard", "Music")
    select_row(app, 2)
    expect(p.locator("#edit-puzzle-type")).to_have_value("none")
    expect(p.locator("#edit-wordle-word")).to_be_hidden()
    select_row(app, 0)
    expect(p.locator("#edit-puzzle-type")).to_have_value("w")
    assert p.locator("#edit-wordle-word").input_value().upper() == "APPLE"
    select_row(app, 1)
    expect(p.locator("#edit-puzzle-type")).to_have_value("q")
    expect(p.locator("#edit-q-text")).to_have_value("What has keys?")
    expect(p.locator("#edit-q-answers")).to_have_value("piano|keyboard")
    expect(p.locator("#edit-q-hint")).to_have_value("Music")
    # a new pin starts with no puzzle
    app.tap_map(*SPOTS[3])
    expect(p.locator("#edit-puzzle-type")).to_have_value("none")
    # deleting clue 1 keeps the others' puzzles attached to the right clues
    p.locator("#clue-list li .del").nth(0).click()
    select_row(app, 0)
    expect(p.locator("#edit-puzzle-type")).to_have_value("q")
    expect(p.locator("#edit-q-text")).to_have_value("What has keys?")


def test_editor_saves_puzzles_in_both_links(app):
    editor(app, 3)
    set_wordle(app, 0, " apple ")
    set_question(app, 1, " What has keys? ", " piano | keyboard ", " Music ")
    player, edit = app.get_links()
    for d in (decode_plain(payload_of(player, "hunt")), decode_plain(payload_of(edit, "edit"))):
        c = d["clues"]
        assert c[0]["p"] == {"k": "w", "a": "APPLE"}
        assert c[1]["p"]["k"] == "q" and c[1]["p"]["q"] == "What has keys?" and c[1]["p"]["h"] == "Music"
        assert [a.strip() for a in c[1]["p"]["a"].split("|")] == ["piano", "keyboard"]
        assert set(c[1]["p"]) == {"k", "q", "a", "h"}
        assert "p" not in c[2]
        assert [x["t"] for x in c] == ["Clue text 1", "Clue text 2", "Clue text 3"]


def test_editor_question_without_hint_has_no_h(app):
    editor(app, 1)
    set_question(app, 0, "Q?", "yes", "   ")
    player, _ = app.get_links()
    assert decode_plain(payload_of(player, "hunt"))["clues"][0]["p"] == {"k": "q", "q": "Q?", "a": "yes"}


def test_editor_switching_type_to_none_drops_the_puzzle(app):
    p = editor(app, 1)
    set_wordle(app, 0, "x")  # invalid, but about to be switched off
    p.locator("#edit-puzzle-type").select_option("none")
    player, _ = app.get_links()
    assert "p" not in decode_plain(payload_of(player, "hunt"))["clues"][0]


@pytest.mark.parametrize("word", ["", "ab", "abcdefghi", "app1e", "ap ple", "naïve", "it's"])
def test_editor_rejects_a_bad_wordle_word(app, word):
    p = editor(app, 3)
    expect(p.locator("#edit-error")).to_be_hidden()
    set_wordle(app, 1, word)
    select_row(app, 2)
    share_fails(app)
    assert selected_row(app) == 1, "the offending clue should be selected"
    expect(p.locator("#edit-wordle-word")).to_be_visible()
    assert "2" in p.locator("#edit-error").inner_text()
    # fixing it clears the way
    p.locator("#edit-wordle-word").fill("Grapes")
    player, _ = app.get_links()
    expect(p.locator("#edit-error")).to_be_hidden()
    assert decode_plain(payload_of(player, "hunt"))["clues"][1]["p"] == {"k": "w", "a": "GRAPES"}


@pytest.mark.parametrize("word", ["cat", "abcdefgh", "Apple"])
def test_editor_accepts_wordle_words_of_3_to_8_letters(app, word):
    editor(app, 1)
    set_wordle(app, 0, word)
    player, _ = app.get_links()
    assert decode_plain(payload_of(player, "hunt"))["clues"][0]["p"] == {"k": "w", "a": word.upper()}


@pytest.mark.parametrize("q,a", [("", "yes"), ("   ", "yes"), ("Q?", ""), ("Q?", "  "), ("Q?", " | |"), ("Q?", "?!|...")])
def test_editor_rejects_an_incomplete_question(app, q, a):
    p = editor(app, 3)
    set_question(app, 2, q, a, "hint")
    select_row(app, 0)
    share_fails(app)
    assert selected_row(app) == 2
    expect(p.locator("#edit-q-text")).to_be_visible()
    assert "3" in p.locator("#edit-error").inner_text()
    p.locator("#edit-q-text").fill("Q?")
    p.locator("#edit-q-answers").fill("yes|no")
    player, _ = app.get_links()
    expect(p.locator("#edit-error")).to_be_hidden()
    assert decode_plain(payload_of(player, "hunt"))["clues"][2]["p"]["a"] == "yes|no"


def test_editor_clue_list_shows_puzzle_badges(app):
    p = editor(app, 3)
    rows = p.locator("#clue-list li")
    assert p.locator("#clue-list li .badge").count() == 0
    set_wordle(app, 0, "apple")
    set_question(app, 2, "Q?", "yes")
    expect(rows.nth(0).locator(".badge")).to_have_count(1)
    expect(rows.nth(1).locator(".badge")).to_have_count(0)
    expect(rows.nth(2).locator(".badge")).to_have_count(1)
    w, q = rows.nth(0).locator(".badge"), rows.nth(2).locator(".badge")
    expect(w).to_be_visible()
    expect(q).to_be_visible()
    assert w.inner_text().strip() and q.inner_text().strip() and w.inner_text() != q.inner_text()
    assert "apple" not in rows.nth(0).inner_text().lower()
    # the row still fits: delete button on screen, badge small
    for i in (0, 2):
        d = rows.nth(i).locator(".del").bounding_box()
        assert d["x"] + d["width"] <= 390
        assert rows.nth(i).locator(".badge").bounding_box()["width"] <= 80
    select_row(app, 0)
    p.locator("#edit-puzzle-type").select_option("none")
    expect(rows.nth(0).locator(".badge")).to_have_count(0)


def test_edit_link_restores_puzzles_skip_and_end(app, make_app):
    p = editor(app, 3)
    p.locator("#edit-title").fill("Big day")
    p.locator("#edit-skip").fill("rescue")
    p.locator("#edit-end").fill("Look behind you!")
    set_wordle(app, 0, "apple")
    set_question(app, 1, "What has keys?", "piano|keyboard", "Music")
    player, edit = app.get_links()
    original = decode_plain(payload_of(player, "hunt"))

    b = make_app().open_url(edit)
    q = b.page
    expect(q.locator("#edit-skip")).to_have_value("rescue")
    expect(q.locator("#edit-end")).to_have_value("Look behind you!")
    rows = q.locator("#clue-list li")
    expect(rows.nth(0).locator(".badge")).to_have_count(1)
    expect(rows.nth(1).locator(".badge")).to_have_count(1)
    expect(rows.nth(2).locator(".badge")).to_have_count(0)
    select_row(b, 0)
    expect(q.locator("#edit-puzzle-type")).to_have_value("w")
    expect(q.locator("#edit-wordle-word")).to_be_visible()
    assert q.locator("#edit-wordle-word").input_value().upper() == "APPLE"
    select_row(b, 1)
    expect(q.locator("#edit-puzzle-type")).to_have_value("q")
    expect(q.locator("#edit-q-text")).to_have_value("What has keys?")
    expect(q.locator("#edit-q-answers")).to_have_value("piano|keyboard")
    expect(q.locator("#edit-q-hint")).to_have_value("Music")
    select_row(b, 2)
    expect(q.locator("#edit-puzzle-type")).to_have_value("none")
    player2, edit2 = b.get_links()
    assert decode_plain(payload_of(player2, "hunt")) == original
    assert decode_plain(payload_of(edit2, "edit")) == original


def test_edit_link_with_bad_puzzle_data_opens_without_a_puzzle(app):
    clues = [clue(C1, {"k": "w", "a": "no"}), clue(C2, {"k": "q", "q": "Q?"}), clue(C3, W)]
    app.open("#edit=" + b64u(hunt_obj(clues, skip={"x": 1}, end=7)))
    p = app.page
    expect(p.locator("#clue-list li")).to_have_count(3)
    expect(p.locator("#clue-list li .badge")).to_have_count(1)
    expect(p.locator("#edit-skip")).to_have_value("")
    expect(p.locator("#edit-end")).to_have_value("")
    select_row(app, 0)
    expect(p.locator("#edit-puzzle-type")).to_have_value("none")
    player, _ = app.get_links()
    d = decode_plain(payload_of(player, "hunt"))
    assert ["p" in c for c in d["clues"]] == [False, False, True]


def test_editor_buttons_stay_pinned_with_puzzle_fields_open(app):
    p = editor(app, 3)
    set_question(app, 2, "What has keys but no locks?", "piano|keyboard", "You can play it")
    p.locator("#edit-q-hint").blur()
    app.settle(200)
    panel = p.locator("#editor-panel").bounding_box()
    assert panel["y"] >= 844 * 0.35
    for sel in ("#btn-share", "#btn-edit-center", "#btn-exit-editor"):
        box = p.locator(sel).bounding_box()
        assert box["y"] >= panel["y"] and box["y"] + box["height"] <= min(844, panel["y"] + panel["height"]) + 0.5, sel
        hit = p.evaluate(
            "([x, y, id]) => { const e = document.elementFromPoint(x, y); return !!e && (e.id === id || !!e.closest('#' + id)); }",
            [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, sel[1:]])
        assert hit, sel
    # every field can be scrolled to and used
    for sel in ("#edit-title", "#edit-skip", "#edit-end", "#edit-q-hint", "#edit-showdist", "#btn-pin-here"):
        p.locator(sel).scroll_into_view_if_needed()
        box = p.locator(sel).bounding_box()
        assert box["y"] >= panel["y"] - 1 and box["y"] + box["height"] <= p.locator("#btn-share").bounding_box()["y"] + 1, sel
    # a validation error is visible without scrolling, and the buttons stay put
    p.locator("#edit-q-answers").fill("")
    p.locator("#edit-title").scroll_into_view_if_needed()
    share_fails(app)
    err = p.locator("#edit-error").bounding_box()
    panel = p.locator("#editor-panel").bounding_box()
    assert err["y"] >= panel["y"] and err["y"] + err["height"] <= 844
    share = p.locator("#btn-share").bounding_box()
    assert share["y"] + share["height"] <= 844


# ------------------------------------------------------- pin / move to GPS
def test_pin_here_adds_a_clue_at_my_position(app):
    p = editor(app, 1)
    lat = app.fix(north(HOME[0], 40), HOME[1] + 0.0003, accuracy=8)
    me = (lat, HOME[1] + 0.0003)
    p.locator("#btn-pin-here").click()
    expect(p.locator("#clue-list li")).to_have_count(2)
    assert selected_row(app) == 1
    expect(p.locator("#pin-warn")).to_be_hidden()
    assert any(near(m, me) for m in markers(app)), (markers(app), me)
    assert any(near(c, me) and c[2] == 25 for c in app.circles() if c[2] != 8)
    expect(p.locator("#clue-text-wrap")).to_be_visible()
    p.locator("#edit-clue-text").fill("Pinned by GPS")
    player, _ = app.get_links()
    c = decode_plain(payload_of(player, "hunt"))["clues"]
    assert len(c) == 2 and near((c[1]["lat"], c[1]["lng"]), me) and c[1]["t"] == "Pinned by GPS"


def test_pin_here_warns_when_accuracy_is_poor_but_still_pins(app):
    p = editor(app, 0)
    app.fix(north(HOME[0], 10), HOME[1], accuracy=30)
    p.locator("#btn-pin-here").click()
    expect(p.locator("#clue-list li")).to_have_count(1)
    expect(p.locator("#pin-warn")).to_be_hidden()  # 30 m is still fine
    app.fix(north(HOME[0], 20), HOME[1], accuracy=47)
    p.locator("#btn-pin-here").click()
    expect(p.locator("#clue-list li")).to_have_count(2)
    expect(p.locator("#pin-warn")).to_be_visible()
    expect(p.locator("#pin-warn")).to_contain_text("47")
    app.fix(north(HOME[0], 30), HOME[1], accuracy=6)
    p.locator("#btn-pin-here").click()
    expect(p.locator("#clue-list li")).to_have_count(3)
    expect(p.locator("#pin-warn")).to_be_hidden()


def test_pin_and_move_do_nothing_without_a_gps_fix(make_app):
    a = make_app()
    a.ctx.clear_permissions()  # location never arrives
    a.open("#edit", wait_fix=False)
    a.settle(400)
    p = a.page
    assert p.evaluate("myPos") is None
    expect(p.locator("#pin-warn")).to_be_hidden()
    p.locator("#btn-pin-here").click()
    expect(p.locator("#pin-warn")).to_be_visible()
    assert p.locator("#pin-warn").inner_text().strip()
    expect(p.locator("#clue-list li")).to_have_count(0)
    assert markers(a) == []
    a.tap_map(*SPOTS[0])
    before = markers(a)
    assert len(before) == 1
    p.locator("#btn-move-here").click()
    expect(p.locator("#pin-warn")).to_be_visible()
    assert markers(a) == before
    expect(p.locator("#clue-list li")).to_have_count(1)


def test_move_here_needs_a_selected_clue_and_moves_pin_and_circle(app):
    p = editor(app, 0)
    expect(p.locator("#btn-move-here")).to_be_disabled()
    for i in range(2):
        app.tap_map(*SPOTS[i])
        p.locator("#edit-clue-text").fill(f"Clue text {i + 1}")
    expect(p.locator("#btn-move-here")).to_be_enabled()
    spots = [app.latlng_at(*s) for s in SPOTS[:2]]
    app.set_radius(60)
    set_question(app, 1, "Q?", "yes")
    select_row(app, 0)
    lat = app.fix(north(HOME[0], -35), HOME[1] - 0.0004, accuracy=5)
    me = (lat, HOME[1] - 0.0004)
    p.locator("#btn-move-here").click()
    expect(p.locator("#pin-warn")).to_be_hidden()
    ms = markers(app)
    assert len(ms) == 2 and any(near(m, me) for m in ms) and any(near(m, spots[1], 2e-5) for m in ms)
    assert not any(near(m, spots[0], 2e-5) for m in ms), "the old pin is gone"
    cs = [c for c in app.circles() if c[2] in (25, 60)]
    assert any(near(c, me) and c[2] == 25 for c in cs) and any(near(c, spots[1], 2e-5) and c[2] == 60 for c in cs)
    assert not any(near(c, spots[0], 2e-5) for c in cs), "the old circle is gone"
    assert selected_row(app) == 0
    expect(p.locator("#clue-list li")).to_have_count(2)
    # poor accuracy: still moves, with a warning
    lat2 = app.fix(north(HOME[0], -60), HOME[1], accuracy=80)
    select_row(app, 1)
    p.locator("#btn-move-here").click()
    expect(p.locator("#pin-warn")).to_be_visible()
    expect(p.locator("#pin-warn")).to_contain_text("80")
    assert any(near(m, (lat2, HOME[1])) for m in markers(app))
    player, _ = app.get_links()
    c = decode_plain(payload_of(player, "hunt"))["clues"]
    assert near((c[0]["lat"], c[0]["lng"]), me) and near((c[1]["lat"], c[1]["lng"]), (lat2, HOME[1]))
    assert c[0]["t"] == "Clue text 1" and c[1]["t"] == "Clue text 2" and c[1]["r"] == 60 and c[1]["p"]["k"] == "q"
    # with every clue deleted there is nothing to move
    p.locator("#btn-share-close").click()
    p.locator("#clue-list li .del").nth(0).click()
    p.locator("#clue-list li .del").nth(0).click()
    expect(p.locator("#clue-list li")).to_have_count(0)
    expect(p.locator("#btn-move-here")).to_be_disabled()


# ================================================================ end to end
def test_end_to_end_build_in_editor_then_play_with_reloads_and_skip(app, make_app):
    # ---- organiser: everything through the editor UI
    p = editor(app)
    p.locator("#edit-title").fill("Newport adventure")
    p.locator("#edit-password").fill("Sea Breeze")
    p.locator("#edit-skip").fill("Lifeboat")
    p.locator("#edit-end").fill("Turn around… 💍")
    texts = ["Where the boats sleep", "Under the old bridge", "The bench with the view"]
    spots = []
    for i, text in enumerate(texts):
        spots.append(app.latlng_at(*SPOTS[i]))
        app.tap_map(*SPOTS[i])
        p.locator("#edit-clue-text").fill(text)
    set_wordle(app, 0, "beach")
    set_question(app, 1, "What has keys but no locks?", "piano|keyboard", "You can play it")
    expect(p.locator("#clue-list li .badge")).to_have_count(2)
    player, edit = app.get_links()
    assert payload_of(player, "hunt").startswith("e.") and payload_of(edit, "edit").startswith("e.")
    for secret in ("beach", "BEACH", "boats", "piano", "Lifeboat", "Turn around"):
        assert secret not in player

    # ---- player: a different phone (fresh context), starting 400 m away
    far = (north(spots[0][0], -400), spots[0][1])
    b = make_app(start=far)
    b.open_url(player)
    q = b.page
    only_overlay(b, "#locked")
    unlock(b, "wrong password")
    expect(q.locator("#locked-err")).to_be_visible()
    unlock(b, "sea breeze")

    # clue 1: Wordle first, clue text hidden
    only_overlay(b, "#puzzle")
    expect(q.locator("#wordle-grid")).to_be_visible()
    assert q.locator("#wordle-grid > *").count() == 30
    assert "boats" not in dom_text(b)
    expect(q.locator("#btn-stuck-puzzle")).to_be_visible()
    type_word(b, "cable")
    assert tiles(b, 0) == list(zip("cable", ["near", "near", "near", "miss", "near"]))
    type_word(b, "be", enter=False)
    b.reload()  # 1) reload in the middle of the Wordle
    only_overlay(b, "#puzzle")  # no password prompt again
    assert tiles(b, 0) == list(zip("cable", ["near", "near", "near", "miss", "near"]))
    assert all(t == ("", "") for t in tiles(b, 1))
    assert key_status(b, "c") == "near" and key_status(b, "l") == "miss"
    assert "boats" not in dom_text(b)
    b.fixes(4, *spots[0])  # standing on the spot does nothing while the puzzle is unsolved
    only_overlay(b, "#puzzle")
    b.fix(*far)
    type_word(b, "beach")
    assert statuses(b, 1) == ["hit"] * 5
    only_overlay(b, "#unlocked")
    expect(q.locator("#unlocked-clue")).to_have_text(texts[0])
    b.reload()  # 2) reload after a solved puzzle
    only_overlay(b, "#unlocked")
    expect(q.locator("#unlocked-clue")).to_have_text(texts[0])
    go(b)
    only_overlay(b, None)
    expect(q.locator("#play-clue")).to_have_text(texts[0])
    expect(q.locator("#play-progress")).to_contain_text("1 of 3")
    b.reload()
    only_overlay(b, None)
    expect(q.locator("#play-clue")).to_have_text(texts[0])

    # walk to clue 1
    b.fixes(2, *spots[0])
    only_overlay(b, None)
    b.fix(*spots[0])
    only_overlay(b, "#found")
    expect(q.locator("#found")).to_contain_text("puzzle")
    assert "bridge" not in dom_text(b)
    q.locator("#btn-next").click()

    # clue 2: question with a hint
    only_overlay(b, "#puzzle")
    expect(q.locator("#puzzle-question")).to_have_text("What has keys but no locks?")
    answer(b, "a door")
    expect(q.locator("#puzzle-wrong")).to_be_visible()
    q.locator("#btn-hint").click()
    expect(q.locator("#puzzle-hint")).to_contain_text("You can play it")
    assert "bridge" not in dom_text(b)
    answer(b, "A Piano!", enter=True)
    only_overlay(b, "#unlocked")
    expect(q.locator("#unlocked-clue")).to_have_text(texts[1])
    go(b)
    expect(q.locator("#play-clue")).to_have_text(texts[1])
    expect(q.locator("#play-progress")).to_contain_text("2 of 3")

    # 3) GPS won't cooperate at clue 2: the organiser gives the skip code
    expect(q.locator("#btn-stuck")).to_be_visible()
    use_code(b, "lifebuoy")
    expect(q.locator("#stuck-err")).to_be_visible()
    q.locator("#stuck-code").fill("lifeboat")
    q.locator("#btn-stuck-go").click()
    only_overlay(b, "#found")
    expect(q.locator("#found-clue")).to_have_text(texts[2])  # clue 3 has no puzzle
    q.locator("#btn-next").click()
    only_overlay(b, None)
    expect(q.locator("#play-clue")).to_have_text(texts[2])
    expect(q.locator("#play-progress")).to_contain_text("3 of 3")

    # clue 3: walk there, and the hunt ends with the custom message
    b.fixes(3, *spots[2])
    only_overlay(b, "#finished")
    expect(q.locator("#finished-sub")).to_have_text("Turn around… 💍")
    b.fix(*far)
    b.reload()
    only_overlay(b, "#finished")
    expect(q.locator("#finished-sub")).to_have_text("Turn around… 💍")


# ============================================================== screenshots
def test_puzzle_screenshots(app, make_app):
    SHOTS2.mkdir(parents=True, exist_ok=True)

    def shot(a, name):
        a.settle(500)
        a.page.screenshot(path=str(SHOTS2 / name))
        assert (SHOTS2 / name).stat().st_size > 5000

    # ---- editor
    p = editor(app)
    p.locator("#edit-title").fill("Newport adventure")
    p.locator("#edit-skip").fill("lifeboat")
    p.locator("#edit-end").fill("Turn around… 💍")
    texts = ["Where the boats sleep at low tide", "Under the old iron bridge", "The bench with the best view"]
    spots = []
    for i, text in enumerate(texts):
        spots.append(app.latlng_at(*SPOTS[i]))
        app.tap_map(*SPOTS[i])
        p.locator("#edit-clue-text").fill(text)
    set_wordle(app, 0, "planet")
    set_question(app, 1, "I have keys but no locks, and space but no room. What am I?", "keyboard|a keyboard|piano", "You are probably near one every day")
    select_row(app, 0)
    p.locator("#edit-wordle-word").scroll_into_view_if_needed()
    p.locator("#edit-wordle-word").blur()
    shot(app, "editor-wordle.png")
    select_row(app, 1)
    p.locator("#edit-q-hint").scroll_into_view_if_needed()
    p.locator("#edit-q-hint").blur()
    shot(app, "editor-question.png")
    player, _ = app.get_links()

    # ---- player
    far = (north(spots[0][0], -400), spots[0][1])
    b = make_app(start=far)
    b.open_url(player)
    q = b.page
    only_overlay(b, "#puzzle")
    for g in ("stormy", "candle", "plates"):
        type_word(b, g)
    type_word(b, "pla", enter=False)
    shot(b, "wordle-mid.png")
    q.locator('#wordle-keys [data-key="enter"]').click()
    for _ in range(3):
        q.locator('#wordle-keys [data-key="back"]').click()
    for g in ("plains", "planes", "plants"):
        type_word(b, g)
    expect(q.locator("#btn-wordle-retry")).to_be_visible()
    shot(b, "wordle-failed.png")
    q.locator("#btn-stuck-puzzle").click()
    expect(q.locator("#stuck")).to_be_visible()
    q.locator("#stuck-code").fill("lifebelt")
    q.locator("#btn-stuck-go").click()
    expect(q.locator("#stuck-err")).to_be_visible()
    shot(b, "stuck.png")
    q.locator("#btn-stuck-cancel").click()
    q.locator("#btn-wordle-retry").click()
    type_word(b, "planet")
    only_overlay(b, "#unlocked")
    shot(b, "unlocked.png")
    go(b)
    b.fixes(3, *spots[0])
    only_overlay(b, "#found")
    shot(b, "found-with-puzzle.png")
    q.locator("#btn-next").click()
    only_overlay(b, "#puzzle")
    shot(b, "question.png")
    answer(b, "a door")
    q.locator("#btn-hint").click()
    expect(q.locator("#puzzle-wrong")).to_be_visible()
    expect(q.locator("#puzzle-hint")).to_be_visible()
    shot(b, "question-wrong-hint.png")
    answer(b, "keyboard")
    go(b)
    b.fixes(3, *spots[1])
    q.locator("#btn-next").click()
    b.fixes(3, *spots[2])
    only_overlay(b, "#finished")
    shot(b, "finished-custom.png")


# ============================================================ safety nets
def test_gps_arrival_while_the_stuck_screen_is_open_is_not_hidden_behind_it(app):
    p = play(app, [clue(C1), clue(C2)], skip="rescue")
    p.locator("#btn-stuck").click()
    expect(p.locator("#stuck")).to_be_visible()
    find_clue(app, C1["lat"], C1["lng"])
    only_overlay(app, "#found")
    expect(p.locator("#found-clue")).to_have_text(C2["t"])
    p.locator("#btn-next").click()
    only_overlay(app, None)
    expect(p.locator("#play-progress")).to_contain_text("2 of 2")


def test_play_again_on_the_final_screen_needs_two_taps(app):
    """One stray tap on the last screen must not wipe the hunt and its final message."""
    p = play(app, [clue(C1, Q)], end="The big finish")
    solve_q(app)
    go(app)
    find_clue(app, C1["lat"], C1["lng"])
    only_overlay(app, "#finished")
    btn = p.locator("#btn-finish-reset")
    label = btn.inner_text()
    btn.click()
    app.settle(400)
    only_overlay(app, "#finished")
    expect(p.locator("#finished-sub")).to_have_text("The big finish")
    assert btn.inner_text() != label, "the button should say that a second tap is needed"
    app.reload()
    only_overlay(app, "#finished")
    expect(btn).to_have_text(label)
    btn.click()
    expect(btn).to_have_text(label, timeout=6000)  # disarms itself
    only_overlay(app, "#finished")
    btn.click()
    btn.click()
    only_overlay(app, "#puzzle")
    app.reload()
    only_overlay(app, "#puzzle")
