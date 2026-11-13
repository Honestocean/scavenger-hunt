"""Follow-up questions at one stop, *italics*, and the bare final screen."""
import pytest
from playwright.sync_api import expect

from test_ui import START, b64u, decode_plain, find_clue, hunt_obj, payload_of
from test_puzzles import C1, C2, Q, answer, clue, dom_text, go, only_overlay, play

Q2 = {"k": "q", "q": "First question?", "a": "pipes", "h": "first hint",
      "n": [{"q": "Second question?", "a": "goats|goat", "h": "second hint"},
            {"q": "Third question?", "a": "three"}]}


# ------------------------------------------------ several questions at one stop
def test_follow_up_questions_are_asked_in_order_before_the_clue(app):
    p = play(app, [clue(C1, Q2)])
    only_overlay(app, "#puzzle")
    expect(p.locator("#puzzle-question")).to_have_text("First question?")
    expect(p.locator("#puzzle-sub")).to_contain_text("1 of 3")
    assert C1["t"] not in dom_text(app)
    assert "Second question?" not in dom_text(app)
    answer(app, "goats")  # right answer to the wrong question
    expect(p.locator("#puzzle-wrong")).to_be_visible()
    answer(app, "Pipes")
    only_overlay(app, "#puzzle")
    expect(p.locator("#puzzle-question")).to_have_text("Second question?")
    expect(p.locator("#puzzle-sub")).to_contain_text("2 of 3")
    expect(p.locator("#puzzle-answer")).to_have_value("")
    expect(p.locator("#puzzle-wrong")).to_be_hidden()
    assert C1["t"] not in dom_text(app)
    answer(app, "pipes")  # the previous answer no longer works
    expect(p.locator("#puzzle-wrong")).to_be_visible()
    answer(app, "goat")
    expect(p.locator("#puzzle-question")).to_have_text("Third question?")
    answer(app, "three")
    only_overlay(app, "#unlocked")
    expect(p.locator("#unlocked-clue")).to_have_text(C1["t"])


def test_follow_up_progress_and_hint_survive_a_reload(app):
    p = play(app, [clue(C1, Q2)])
    answer(app, "pipes")
    expect(p.locator("#puzzle-question")).to_have_text("Second question?")
    expect(p.locator("#puzzle-hint")).to_be_hidden()  # the first hint does not leak across
    expect(p.locator("#btn-hint")).to_be_visible()
    p.locator("#btn-hint").click()
    expect(p.locator("#puzzle-hint")).to_contain_text("second hint")
    app.reload()
    only_overlay(app, "#puzzle")
    expect(p.locator("#puzzle-question")).to_have_text("Second question?")
    expect(p.locator("#puzzle-hint")).to_contain_text("second hint")
    answer(app, "goats")
    expect(p.locator("#puzzle-question")).to_have_text("Third question?")
    expect(p.locator("#btn-hint")).to_be_hidden()  # third question has no hint
    expect(p.locator("#puzzle-hint")).to_be_hidden()


def test_single_question_shows_no_counter(app):
    p = play(app, [clue(C1, Q)])
    assert " of 1" not in p.locator("#puzzle-sub").inner_text().replace("clue 1 of 1", "")


def test_skip_code_skips_all_remaining_questions_at_the_stop(app):
    p = play(app, [clue(C1, Q2)], skip="skip")
    answer(app, "pipes")
    p.locator("#btn-stuck-puzzle").click()
    p.locator("#stuck-code").fill("SKIP")
    p.locator("#btn-stuck-go").click()
    only_overlay(app, "#unlocked")


def test_restart_clears_follow_up_progress(app):
    p = play(app, [clue(C1, Q2), clue(C2)])
    answer(app, "pipes")
    answer(app, "goats")
    answer(app, "three")
    go(app)
    p.locator("#btn-play-reset").click()
    p.locator("#btn-play-reset").click()
    only_overlay(app, "#puzzle")
    expect(p.locator("#puzzle-question")).to_have_text("First question?")


@pytest.mark.parametrize("bad", [
    "nope", [1, 2], [{"q": "", "a": "x"}], [{"q": "ok?", "a": ""}], [None], {"q": "x", "a": "y"},
])
def test_bad_follow_up_data_is_dropped_not_fatal(app, bad):
    p = play(app, [clue(C1, dict(Q, n=bad))])
    only_overlay(app, "#puzzle")
    answer(app, "piano")
    only_overlay(app, "#unlocked")


def test_editor_follow_up_questions_round_trip(app, make_app):
    app.open("#edit=" + b64u(hunt_obj([clue(C1, Q2)])))
    p = app.page
    p.locator("#clue-list li").first.click()
    more = p.locator("#edit-q-more")
    expect(more).to_be_visible()
    assert more.input_value().splitlines() == ["Second question? = goats|goat = second hint", "Third question? = three"]
    more.fill("What was playing? = pipes|bagpipes\nLove of… = goats = think farm")
    player, edit = app.get_links()
    d = decode_plain(payload_of(player, "hunt"))
    assert d["clues"][0]["p"]["n"] == [
        {"q": "What was playing?", "a": "pipes|bagpipes"},
        {"q": "Love of…", "a": "goats", "h": "think farm"},
    ]
    assert decode_plain(payload_of(edit, "edit")) == d


def test_editor_rejects_a_follow_up_line_without_an_answer(app):
    app.open("#edit=" + b64u(hunt_obj([clue(C1, Q)])))
    p = app.page
    p.locator("#clue-list li").first.click()
    p.locator("#edit-q-more").fill("A question with no answer")
    p.locator("#btn-share").click()
    expect(p.locator("#share")).to_be_hidden()
    expect(p.locator("#edit-error")).to_be_visible()


def test_editor_without_follow_ups_saves_no_n_field(app):
    app.open("#edit=" + b64u(hunt_obj([clue(C1, Q)])))
    player, _ = app.get_links()
    assert "n" not in decode_plain(payload_of(player, "hunt"))["clues"][0]["p"]


# ------------------------------------------------------------------- italics
def test_asterisks_make_italics_in_questions(app):
    q = {"k": "q", "q": "I wrote *about freedom* and 2 * 3 = 6", "a": "mill"}
    p = play(app, [clue(C1, q)])
    em = p.locator("#puzzle-question em")
    expect(em).to_have_count(1)
    expect(em).to_have_text("about freedom")
    expect(p.locator("#puzzle-question")).to_have_text("I wrote about freedom and 2 * 3 = 6")
    assert p.evaluate("getComputedStyle(document.querySelector('#puzzle-question em')).fontStyle") == "italic"


def test_italics_markup_cannot_inject_html(app):
    q = {"k": "q", "q": "*<img src=x onerror=window.__x=1>* <b id=xe>b</b>", "a": "x"}
    p = play(app, [clue(C1, q)])
    expect(p.locator("#puzzle-question img")).to_have_count(0)
    expect(p.locator("#xe")).to_have_count(0)
    assert p.evaluate("window.__x") is None


def test_asterisks_make_italics_in_clue_text(app):
    p = play(app, [clue(C1, t="Go to the *old* oak")])
    expect(p.locator("#play-clue em")).to_have_text("old")
    expect(p.locator("#play-clue")).to_have_text("Go to the old oak")


# --------------------------------------------------------- bare final screen
def finish(app, **extra):
    p = play(app, [clue(C1)], **extra)
    find_clue(app, C1["lat"], C1["lng"])
    only_overlay(app, "#finished")
    return p


def test_custom_final_message_is_the_only_thing_on_the_final_screen(app):
    p = finish(app, end="Eleanor Sarah Bradshaw")
    expect(p.locator("#finished-sub")).to_have_text("Eleanor Sarah Bradshaw")
    visible_text = p.locator("#finished").inner_text().strip()
    assert visible_text == "Eleanor Sarah Bradshaw"
    expect(p.locator("#finished h1")).to_be_hidden()
    expect(p.locator("#btn-finish-reset")).to_be_hidden()
    box = p.locator("#finished-sub").bounding_box()
    assert 0 <= box["x"] and box["x"] + box["width"] <= 390
    assert 844 * 0.2 < box["y"] + box["height"] / 2 < 844 * 0.7, "message should sit around the middle"
    size = p.evaluate("parseFloat(getComputedStyle(document.getElementById('finished-sub')).fontSize)")
    assert size >= 30
    # nothing from the play screen shows through or around it
    assert p.evaluate("getComputedStyle(document.getElementById('finished')).backgroundColor").startswith("rgb(")
    app.reload()
    only_overlay(app, "#finished")
    assert p.locator("#finished").inner_text().strip() == "Eleanor Sarah Bradshaw"


def test_long_custom_final_message_still_fits(app):
    p = finish(app, end="W" * 300)
    box = p.locator("#finished-sub").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390


def test_default_final_screen_is_unchanged(app):
    p = finish(app)
    expect(p.locator("#finished h1")).to_be_visible()
    expect(p.locator("#btn-finish-reset")).to_be_visible()


def test_ten_quick_taps_on_the_final_message_restart(app):
    p = finish(app, end="Eleanor Sarah Bradshaw")
    for _ in range(9):
        p.locator("#finished-sub").click()
    app.settle(1500)
    only_overlay(app, "#finished")  # nine taps do nothing
    app.fix(*START)
    for _ in range(10):
        p.locator("#finished-sub").click()
    p.wait_for_function("typeof map !== 'undefined'")
    expect(p.locator("#finished")).to_be_hidden()
    expect(p.locator("#play-clue")).to_have_text(C1["t"])


def test_slow_taps_on_the_final_message_do_not_restart(app):
    p = finish(app, end="Eleanor Sarah Bradshaw")
    for _ in range(12):
        p.locator("#finished-sub").click()
        app.settle(1100)
    only_overlay(app, "#finished")
