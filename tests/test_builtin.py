"""A hunt stored in the app itself, opened by a short name."""
from playwright.sync_api import expect

from test_ui import b64u, hunt_obj
from test_puzzles import C1, clue


def test_built_in_hunt_opens_by_short_name(app):
    app.open("#hunt=newport", wait_fix=False)
    p = app.page
    expect(p.locator("#link-error")).to_be_hidden()
    expect(p.locator("#locked")).to_be_visible()          # it is password-protected
    assert p.evaluate("storeId") == "builtin:newport"
    assert p.evaluate("payload === BUILT_IN.newport && encryptedShapeOk(payload)")


def test_built_in_name_is_not_case_sensitive(app):
    app.open("#hunt=Newport", wait_fix=False)
    expect(app.page.locator("#locked")).to_be_visible()
    assert app.page.evaluate("storeId") == "builtin:newport"


def test_built_in_hunt_opens_in_the_editor_too(app):
    app.open("#edit=newport", wait_fix=False)
    expect(app.page.locator("#locked")).to_be_visible()


def test_unknown_short_name_is_reported(app):
    app.open("#hunt=nosuchhunt", wait_fix=False)
    expect(app.page.locator("#link-error")).to_be_visible()


def test_ordinary_links_still_file_progress_under_their_payload(app):
    payload = b64u(hunt_obj([clue(C1)]))
    app.open("#hunt=" + payload)
    assert app.page.evaluate("storeId") == payload
    assert app.page.evaluate("Object.prototype.hasOwnProperty.call(BUILT_IN, 'constructor')") is False
