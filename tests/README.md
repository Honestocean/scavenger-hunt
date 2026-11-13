# UI tests

Headless-Chrome tests for `index.html`, written from the feature spec:
edit link + restore, map follow, per-clue radius, per-hunt password,
old-link compatibility and regressions (test mode, saved progress,
two-tap restart, non-ASCII text) in `test_ui.py`; puzzles (Wordle and
question), the play flow around them, reload at every stage, the skip code,
the final message, the editor's puzzle fields and GPS pin buttons, and one
end-to-end hunt in `test_puzzles.py`. The shared fixtures (local server,
browser, `app`, `make_app`) are in `conftest.py`.

## Prerequisites

1. **Python Playwright + pytest, and a Chrome binary.**
   `pip install playwright pytest`. The tests launch the Chrome at
   `/usr/bin/google-chrome` (override with `CHROME_PATH=/path/to/chrome`);
   no Playwright browser download is needed.
2. **A local copy of Leaflet 1.9.4, outside the repo.**
   `mkdir -p /home/claude/work && cd /home/claude/work && npm install leaflet@1.9.4`
   The tests expect `/home/claude/work/node_modules/leaflet/dist`
   (override with `LEAFLET_DIST=/path/to/leaflet/dist`).

## Run

From the repo root:

    PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests

(`PYTHONDONTWRITEBYTECODE=1` and `tests/pytest.ini` keep `__pycache__` and
`.pytest_cache` out of the repo.) One test: add `-k name`. The run takes
about five minutes.

## How it works

- The suite starts its own `http.server` for the repo on a free
  `http://localhost` port (a secure context, so geolocation and
  `crypto.subtle` work) and stops it at the end.
- No internet is needed: the two cdnjs Leaflet URLs in `index.html` are
  answered from the local Leaflet copy, and map tile requests get a blank
  grey PNG. `index.html` itself is not modified for testing.
- Each test gets a fresh phone-sized context (390x844, touch, mobile) with
  geolocation granted. GPS is driven with `context.set_geolocation`; the
  `fix()` helper delivers exactly one fix and waits for the app to receive it.
- A small init script counts delivered GPS fixes and records which
  WebCrypto algorithms the page uses (to check AES-GCM / PBKDF2).
- `test_screenshots` writes `editor.png`, `share.png`, `locked.png`,
  `locked-error.png`, `play.png` and `found.png` to `/home/claude/work/shots`
  (override with `SHOTS_DIR=`).
- `test_puzzle_screenshots` writes `wordle-mid.png`, `wordle-failed.png`,
  `question.png`, `question-wrong-hint.png`, `unlocked.png`,
  `found-with-puzzle.png`, `finished-custom.png`, `stuck.png`,
  `editor-wordle.png` and `editor-question.png` to `/home/claude/work/shots2`
  (override with `SHOTS2_DIR=`).
