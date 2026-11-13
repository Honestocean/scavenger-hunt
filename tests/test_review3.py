"""More fixes from the second review: two-tap exit and delete in the editor, follow-up
question parsing, and tall overlays that scroll from the top."""
import pytest
from playwright.sync_api import expect

from test_ui import b64u, decode_plain, hunt_obj, payload_of
from test_puzzles import C1, C2, C3, Q, clue, editor, find_clue, go, only_overlay, play, solve_q


# ------------------------------------------------------------- editor: Exit
def test_exit_needs_two_taps(app):
    p = editor(app, 2)
    btn = p.locator("#btn-exit-editor")
    label = btn.inner_text()
    btn.click()
    app.settle(500)
    expect(p.locator("#editor-panel")).to_be_visible()     # still in the editor, nothing lost
    expect(p.locator("#clue-list li")).to_have_count(2)
    assert btn.inner_text() != label and "again" in btn.inner_text().lower()
    box, panel = btn.bounding_box(), p.locator("#editor-panel").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390 and box["y"] + box["height"] <= panel["y"] + panel["height"] + 0.5
    for sel in ("#btn-share", "#btn-edit-center"):          # the longer label hides nothing
        b = p.locator(sel).bounding_box()
        assert b["x"] >= 0 and b["width"] >= 60 and b["y"] + b["height"] <= panel["y"] + panel["height"] + 0.5, sel
    btn.click()
    expect(p.locator("#test-panel")).to_be_visible()
    expect(p.locator("#editor-panel")).to_be_hidden()


def test_exit_disarms_after_three_seconds(app):
    p = editor(app, 1)
    btn = p.locator("#btn-exit-editor")
    label = btn.inner_text()
    btn.click()
    app.settle(3400)
    assert btn.inner_text() == label
    btn.click()                                             # only arms it again
    app.settle(500)
    expect(p.locator("#editor-panel")).to_be_visible()
    expect(p.locator("#clue-list li")).to_have_count(1)


# ----------------------------------------------------------- editor: delete
def texts(p):
    return p.locator("#clue-list li .txt").all_inner_texts()


def test_delete_needs_two_taps_on_the_same_row(app):
    p = editor(app, 3)
    dels = p.locator("#clue-list li .del")
    dels.nth(1).click()
    app.settle(300)
    assert texts(p) == ["Clue text 1", "Clue text 2", "Clue text 3"]
    assert "sure" in dels.nth(1).inner_text().lower()
    assert "sure" not in dels.nth(0).inner_text().lower()
    box = dels.nth(1).bounding_box()
    assert box["x"] + box["width"] <= 390
    dels.nth(1).click()
    assert texts(p) == ["Clue text 1", "Clue text 3"]
    for i in range(2):                                      # nothing is left armed
        assert "sure" not in dels.nth(i).inner_text().lower()


def test_delete_taps_on_different_rows_delete_nothing(app):
    p = editor(app, 3)
    dels = p.locator("#clue-list li .del")
    dels.nth(0).click()
    dels.nth(2).click()
    app.settle(300)
    assert texts(p) == ["Clue text 1", "Clue text 2", "Clue text 3"]
    assert "sure" not in dels.nth(0).inner_text().lower()   # only the last one tapped is armed
    assert "sure" in dels.nth(2).inner_text().lower()
    dels.nth(2).click()
    assert texts(p) == ["Clue text 1", "Clue text 2"]


def test_delete_disarms_after_three_seconds(app):
    p = editor(app, 2)
    d = p.locator("#clue-list li .del").nth(0)
    d.click()
    app.settle(3400)
    assert "sure" not in d.inner_text().lower()
    d.click()
    app.settle(300)
    assert texts(p) == ["Clue text 1", "Clue text 2"]


def test_first_delete_tap_does_not_select_the_row(app):
    p = editor(app, 2)                                       # row 2 is selected
    p.locator("#clue-list li .del").nth(0).click()
    assert p.evaluate(
        "[...document.querySelectorAll('#clue-list li')].findIndex(li => li.classList.contains('selected'))") == 1


# ------------------------------------------------- follow-up question lines
def follow_ups(app, text):
    app.open("#edit=" + b64u(hunt_obj([clue(C1, Q)])))
    p = app.page
    p.locator("#clue-list li").first.click()
    p.locator("#edit-q-more").fill(text)
    return p


@pytest.mark.parametrize("line, want", [
    ("What is 2+2=? = 4|four", {"q": "What is 2+2=?", "a": "4|four"}),
    ("Solve x=3+4 = 7 = add them", {"q": "Solve x=3+4", "a": "7", "h": "add them"}),
    ("a=b=c=d, how many letters? = 4", {"q": "a=b=c=d, how many letters?", "a": "4"}),
    ("Colour? = red = hint: r=e=d", {"q": "Colour?", "a": "red", "h": "hint: r=e=d"}),
    ("Q?=a", {"q": "Q?", "a": "a"}),
    ("Q?=a=h", {"q": "Q?", "a": "a", "h": "h"}),
    ("Q? =a", {"q": "Q?", "a": "a"}),
    ("Q?= a =h", {"q": "Q?", "a": "a", "h": "h"}),
    ("Q? = a = ", {"q": "Q?", "a": "a"}),
    ("Q? = a =", {"q": "Q?", "a": "a"}),
    ("Q?\t=\ta", {"q": "Q?", "a": "a"}),
])
def test_follow_up_lines_are_split_on_spaced_equals(app, line, want):
    p = follow_ups(app, "First? = one\n" + line)
    player, edit = app.get_links()
    d = decode_plain(payload_of(player, "hunt"))
    assert d["clues"][0]["p"]["n"] == [{"q": "First?", "a": "one"}, want]
    # and it reads back into the editor as something that saves the same again
    assert p.evaluate("location.href") == edit
    app.reload()            # (the address bar is already on the edit link)
    p.locator("#clue-list li").first.click()
    player2, _ = app.get_links()
    assert decode_plain(payload_of(player2, "hunt")) == d


@pytest.mark.parametrize("line", [
    "Is 1 = 1 or 2 = 2? = both",
    "Q? = a = hint = more",
    "Q?=a=h=x",
    "Q? = a =h",          # the answers would silently swallow the hint
])
def test_ambiguous_follow_up_lines_are_rejected_with_a_clear_message(app, line):
    p = follow_ups(app, "First? = one\n" + line)
    p.locator("#btn-share").click()
    expect(p.locator("#edit-error")).to_be_visible()
    app.settle(300)
    expect(p.locator("#share")).to_be_hidden()
    msg = p.locator("#edit-error").inner_text()
    assert "Clue 1" in msg and "“ = ”" in msg and "question 2" in msg.lower(), msg
    assert p.evaluate("document.activeElement.id") == "edit-q-more"
    # fixing the line is enough
    p.locator("#edit-q-more").fill("First? = one\nIs 1=1 or 2=2? = both")
    expect(p.locator("#edit-error")).to_be_hidden()
    player, _ = app.get_links()
    assert decode_plain(payload_of(player, "hunt"))["clues"][0]["p"]["n"][1] == {"q": "Is 1=1 or 2=2?", "a": "both"}


# ------------------------------------------------------------ tall overlays
LONG = " ".join(["Wander westward where the willows weep"] * 14)[:500]
assert len(LONG) == 500
SMALL = {"width": 360, "height": 560}


def reachable(app, overlay, first, last):
    """The overlay scrolls, and both its first and last element can be brought fully into view."""
    p = app.page
    o = p.locator(overlay)
    expect(o).to_be_visible()
    m = o.evaluate("e => ({ s: e.scrollHeight, c: e.clientHeight, top: e.scrollTop })")
    assert m["s"] > m["c"] + 50, "the test needs content taller than the screen"
    assert m["top"] == 0, "a tall overlay opens at its top"
    h = p.viewport_size["height"]
    top = p.locator(first).bounding_box()
    assert 0 <= top["y"] and top["y"] + top["height"] <= h, top
    o.evaluate("e => { e.scrollTop = e.scrollHeight; }")
    b = p.locator(last).bounding_box()
    assert 0 <= b["y"] and b["y"] + b["height"] <= h, b
    o.evaluate("e => { e.scrollTop = 0; }")
    top2 = p.locator(first).bounding_box()
    assert abs(top2["y"] - top["y"]) < 1


def centred(app, overlay, first, last):
    p = app.page
    expect(p.locator(overlay)).to_be_visible()
    o = p.locator(overlay).bounding_box()
    a, b = p.locator(first).bounding_box(), p.locator(last).bounding_box()
    above, below = a["y"] - o["y"], o["y"] + o["height"] - (b["y"] + b["height"])
    assert above > 40 and abs(above - below) <= 2, (above, below)


def test_long_clue_on_the_found_and_unlocked_screens_can_be_scrolled(app):
    app.page.set_viewport_size(SMALL)
    p = play(app, [clue(C1), clue(C2, t=LONG), clue(C3, Q, t=LONG)])
    find_clue(app, C1["lat"], C1["lng"])
    only_overlay(app, "#found")
    expect(p.locator("#found-clue")).to_have_text(LONG)
    reachable(app, "#found", "#found h1", "#btn-next")
    p.locator("#found").evaluate("e => { e.scrollTop = e.scrollHeight; }")
    p.locator("#btn-next").click()
    find_clue(app, C2["lat"], C2["lng"])
    only_overlay(app, "#found")
    assert p.locator("#found").evaluate("e => e.scrollTop") == 0   # not left scrolled down from last time
    p.locator("#btn-next").click()
    solve_q(app)
    only_overlay(app, "#unlocked")
    reachable(app, "#unlocked", "#unlocked h1", "#btn-unlocked-go")
    p.locator("#unlocked").evaluate("e => { e.scrollTop = e.scrollHeight; }")
    p.locator("#btn-unlocked-go").click()                    # really tappable down there
    only_overlay(app, None)


def test_short_clue_stays_centred_on_the_found_and_unlocked_screens(app):
    p = play(app, [clue(C1), clue(C2, Q)])
    find_clue(app, C1["lat"], C1["lng"])
    centred(app, "#found", "#found h1", "#btn-next")
    p.locator("#btn-next").click()
    solve_q(app)
    centred(app, "#unlocked", "#unlocked h1", "#btn-unlocked-go")


def test_welcome_screen_scrolls_from_the_top_when_it_is_tall(app):
    app.page.set_viewport_size({"width": 320, "height": 300})
    p = play(app, [clue(C1, Q), clue(C2)], intro=1, title="A fairly long title for a hunt " * 3)
    reachable(app, "#intro", "#intro-title", "#btn-intro-go")
    p.locator("#intro").evaluate("e => { e.scrollTop = e.scrollHeight; }")
    p.locator("#btn-intro-go").click()
    only_overlay(app, "#puzzle")


def test_welcome_screen_stays_centred_when_it_is_short(app):
    play(app, [clue(C1), clue(C2)], intro=1, title="Short")
    centred(app, "#intro", "#intro .card", "#intro .card")
