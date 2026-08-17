"""
Guardrails on the frontend (index.html / styles.css / app.js).

There's no build step, bundler, or type checker behind any of it, so
nothing else catches a syntax error or a stale DOM reference. These are deliberately
NOT tests of appearance — no assertions about colours, copy, or markup,
because those break on every cosmetic change and get ignored.

They only assert things that have actually broken the app, plus a syntax
check. Each has an INCIDENT note so nobody deletes one as hypothetical.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
INDEX = STATIC / "index.html"
STYLES = STATIC / "styles.css"
APP_JS = STATIC / "app.js"


def _strip_comments(js: str) -> str:
    """Remove comments so prose discussing a pattern isn't mistaken for it."""
    js = re.sub(r"//.*", "", js)
    return re.sub(r"/\*[\s\S]*?\*/", "", js)


# --------------------------------------------------------------------------
# INCIDENT: a null-reference on a removed element threw at load, which stops
# ALL later script execution — the whole page silently dies. A syntax error
# does the same. Nothing else in the toolchain catches either.
# --------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_frontend_javascript_parses(frontend_source, tmp_path):
    # A real file rather than stdin: `node --check -` waits on stdin and hangs.
    script = tmp_path / "inline.js"
    script.write_text(f"(function(){{{frontend_source}\n}})();", encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(script)],
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, f"inline JS has a syntax error:\n{result.stderr}"


# --------------------------------------------------------------------------
# INCIDENT: a regex lookbehind in linkifyGlossary threw outright on iOS
# WebKit below 16.4 ("The string did not match the expected pattern"),
# taking down summary rendering entirely. Lookbehind must stay out.
# --------------------------------------------------------------------------

def test_no_regex_lookbehind(frontend_source):
    code = _strip_comments(frontend_source)
    assert not re.search(r"\(\?<[!=]", code), (
        "regex lookbehind is unsupported on iOS WebKit < 16.4 and throws at "
        "construction — it previously broke all summary rendering"
    )


# --------------------------------------------------------------------------
# INCIDENT: key-point and view-toggle controls were real <button>s, which on
# iOS get a native pressed-state fill that CSS cannot suppress. They were
# deliberately changed to <div role="button">. Changing them back
# reintroduces the "everything gets highlighted" bug.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cls", ["key-point-toggle", "view-toggle-btn"])
def test_tap_targets_are_not_native_buttons(cls, frontend_source):
    assert f'<button class="{cls}' not in frontend_source, (
        f".{cls} must stay a <div role=\"button\">: native <button> shows an "
        "un-suppressable pressed fill on iOS"
    )


def test_those_tap_targets_remain_keyboard_accessible(frontend_source):
    """Non-native controls lose Enter/Space for free — it's wired manually."""
    assert "key-point-toggle, .view-toggle-btn" in frontend_source or (
        "'.key-point-toggle'" in frontend_source and "keydown" in frontend_source
    ), "div-based controls need an explicit keydown handler"


# --------------------------------------------------------------------------
# INCIDENT: iOS force-zooms the page when a focused input's font-size is
# under 16px, and the page can get stuck zoomed after a layout change.
# --------------------------------------------------------------------------

def test_inputs_are_at_least_16px():
    css = STYLES.read_text(encoding="utf-8")
    assert re.search(r"input,\s*textarea,\s*select\s*\{[^}]*font-size:\s*16px", css), (
        "the global 16px input rule prevents iOS auto-zoom — do not lower it"
    )


# --------------------------------------------------------------------------
# INCIDENT: loadQuestions() was placed in the wrong block by a scripted
# edit and never ran at page load, so every Questions panel sat on
# "Loading…" forever. It must be awaited during bootstrap.
# --------------------------------------------------------------------------

def test_question_set_is_awaited_during_bootstrap(frontend_source):
    assert "await loadQuestions()" in frontend_source, (
        "QUESTIONS is read at render time and nothing re-renders the cards, "
        "so it must be loaded before any view renders"
    )


# --------------------------------------------------------------------------
# Rendering resilience: glossary linking is decorative, and a throw in it
# previously surfaced to the user as "summarizing failed" while hiding a
# summary that had generated fine.
# --------------------------------------------------------------------------

def test_glossary_linkify_has_a_fallback(frontend_source):
    assert "linkifyGlossaryUnsafe" in frontend_source, (
        "linkifyGlossary must wrap the risky path so a rendering bug can't "
        "take the whole summary down"
    )


# --------------------------------------------------------------------------
# Every element the script looks up by id must exist in the markup, or the
# lookup returns null and the next property access throws at load.
# --------------------------------------------------------------------------

def _static_markup() -> str:
    """The HTML markup. Now that JS lives in app.js, index.html is markup
    only — but keep stripping any <script> block so an inline one added
    later can't reintroduce false positives."""
    html = INDEX.read_text(encoding="utf-8")
    return re.sub(r"<script>[\s\S]*?</script>", "", html)


def test_every_getelementbyid_target_exists(frontend_source):
    """
    INCIDENT: this test's first run found `summaryText` still being assigned
    to after its element was deleted — a TypeError on every summarize, which
    the submit handler then reported to the user as a failed summary.

    Counts three ways an element can legitimately exist: declared in static
    markup, injected via an innerHTML template containing id="...", or
    created in code with `el.id = '...'`.
    """
    declared = set(re.findall(r'id="([^"]+)"', _static_markup()))
    injected = set(re.findall(r'id="([A-Za-z][\w-]*)"', frontend_source))
    assigned = set(re.findall(r"\.id\s*=\s*['\"]([^'\"]+)['\"]", frontend_source))
    referenced = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", frontend_source))

    missing = sorted(referenced - declared - injected - assigned)
    assert not missing, (
        f"script references ids that are never created anywhere: {missing} — "
        "getElementById returns null and the next access throws, killing all JS"
    )


def test_element_ids_are_unique():
    """Duplicates make getElementById silently pick the first match."""
    ids = re.findall(r'id="([^"]+)"', _static_markup())
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicates, f"duplicate element ids in markup: {duplicates}"
