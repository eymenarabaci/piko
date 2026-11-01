"""Static contract tests for the Phase 11 Piko mascot assets.

These tests do not start a server and do not execute browser code. They pin the
JSON/event contract, the nine-state sprite, the reduced-motion fallback and the
heartbeat rule so the later app.js mount cannot silently break them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
SPRITE = UI / "mascot-sprite.svg"
CSS = UI / "mascot.css"
JS = UI / "mascot.js"
DOC = UI / "MASCOT_API.md"

STATES = (
    "idle",
    "konusuyor",
    "yuruyor",
    "calisiyor",
    "dagitiyor",
    "teslim",
    "bekliyor",
    "takildi",
    "uyuyor",
)


def read(path: Path) -> str:
    assert path.is_file(), f"missing mascot asset: {path}"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Sprite
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", STATES)
def test_sprite_has_state_symbol(state: str) -> None:
    sprite = read(SPRITE)
    assert re.search(rf'<symbol\s+id="piko-{re.escape(state)}"', sprite), state


def test_sprite_has_exactly_the_nine_state_symbols() -> None:
    sprite = read(SPRITE)
    ids = re.findall(r'<symbol\s+id="(piko-[a-z]+)"', sprite)
    assert sorted(ids) == sorted(f"piko-{state}" for state in STATES)
    assert len(ids) == 9


def test_sprite_marks_each_state_with_data_state() -> None:
    sprite = read(SPRITE)
    values = re.findall(r'data-state="([a-z]+)"', sprite)
    assert sorted(values) == sorted(STATES)


def test_sprite_is_self_contained() -> None:
    sprite = read(SPRITE)
    assert sprite.lstrip().startswith("<svg")
    assert 'xmlns="http://www.w3.org/2000/svg"' in sprite
    assert "<script" not in sprite.lower()
    assert "<image" not in sprite.lower()
    assert "xlink:href" not in sprite
    assert not re.search(r'https?://(?!www\.w3\.org)', sprite)


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", STATES)
def test_css_has_state_selector(state: str) -> None:
    css = read(CSS)
    assert f'[data-state="{state}"]' in css, state


def test_css_has_state_keyframes() -> None:
    css = read(CSS)
    names = set(re.findall(r"@keyframes\s+(piko-[a-z]+)", css))
    for name in (
        "piko-speak",
        "piko-walk",
        "piko-work",
        "piko-fanout",
        "piko-deliver",
        "piko-wait",
        "piko-stuck",
        "piko-sleep",
    ):
        assert name in names, name


def test_css_idle_state_has_no_animation() -> None:
    css = read(CSS)
    assert '[data-state="idle"] .piko-mascot-figure' not in css


def test_css_reduced_motion_disables_animations_and_keeps_icon() -> None:
    css = read(CSS)
    assert re.search(r"@media\s*\(\s*prefers-reduced-motion:\s*reduce\s*\)", css)
    block = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert re.search(r"animation:\s*none\s*!important", block)
    assert re.search(r"transition:\s*none\s*!important", block)
    assert ".piko-mascot-figure" in block and "display: none" in block
    assert ".piko-state-icon" in block and "display: inline-flex" in block


def test_css_has_no_external_imports() -> None:
    css = read(CSS)
    assert "@import" not in css
    assert not re.search(r"url\(\s*['\"]?https?://", css)


# ---------------------------------------------------------------------------
# JavaScript API
# ---------------------------------------------------------------------------

def test_js_exposes_init_mascot_api() -> None:
    js = read(JS)
    assert "function initMascot" in js
    assert "initMascot.STATES" in js
    assert "global.initMascot = initMascot" in js
    assert re.search(r"dispatch\s*:\s*dispatch", js)
    assert re.search(r"setHeartbeatAlive\s*:\s*setHeartbeatAlive", js)
    assert re.search(r"getState\s*:\s*snapshot", js)


def test_js_declares_all_nine_states() -> None:
    js = read(JS)
    block = js.split("var STATES = [", 1)[1].split("];", 1)[0]
    declared = re.findall(r"'([a-z]+)'", block)
    assert declared == list(STATES)


def test_js_heartbeat_timeout_is_120_seconds() -> None:
    js = read(JS)
    match = re.search(r"HEARTBEAT_TIMEOUT_MS\s*=\s*(\d+)", js)
    assert match, "HEARTBEAT_TIMEOUT_MS missing"
    assert int(match.group(1)) == 120000
    assert "setInterval" in js and "setHeartbeatAlive" in js


def test_js_accepts_the_documented_event_contract() -> None:
    js = read(JS)
    for key in ("event", "work_id", "reason", "pack"):
        assert key in js, key
    for event in ("work.active", "deliverable.delivered", "work.waiting",
                  "work.blocked", "heartbeat.silent", "daemon.stale"):
        assert event in js, event


def test_js_maps_health_and_work_record_reports() -> None:
    js = read(JS)
    for field in ("health_report", "daemon_alive", "stale_runs", "active_runs",
                  "kill_switch", "work_record", "deliverable_status"):
        assert field in js, field
    for status in ("open", "active", "waiting", "blocked", "done", "cancelled"):
        assert re.search(rf"\b{status}\s*:", js), status


def test_js_has_no_external_dependency_or_server_call() -> None:
    js = read(JS)
    assert "require(" not in js.replace("typeof module", "")
    assert "fetch(" not in js
    assert "XMLHttpRequest" not in js
    assert "WebSocket" not in js
    assert "createServer" not in js
    assert "listen(" not in js


# ---------------------------------------------------------------------------
# Documentation / mount contract
# ---------------------------------------------------------------------------

def test_doc_records_contract_and_mount_notes() -> None:
    doc = read(DOC)
    for term in ("initMascot", "dispatch", "setHeartbeatAlive", "HealthReport",
                 "work_record", "daemon_alive", "prefers-reduced-motion",
                 "/api/health", "120"):
        assert term in doc, term
    for state in STATES:
        assert state in doc, state
