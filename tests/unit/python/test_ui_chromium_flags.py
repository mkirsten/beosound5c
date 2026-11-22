"""Chromium is launched with exactly one feature flag of each kind.

Chromium honours only the *last* --enable-features and the *last*
--disable-features on a command line; earlier occurrences are silently
dropped. services/ui.sh had accumulated four --disable-features flags, so
only InfiniteSessionRestore was really being disabled — TranslateUI,
IsolateOrigins/site-per-process and MediaRouter were not, despite
appearing in the command.
"""
from __future__ import annotations

import re
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[3] / "services"
# Chromium is launched from the xinit client, which ui.sh runs from its own file.
UI_SH = SERVICES / "ui-session.sh"


def _flags(kind: str) -> list[str]:
    """Every --<kind>-features=... actually passed (comments excluded)."""
    src = "\n".join(line for line in UI_SH.read_text().splitlines()
                    if not line.strip().startswith("#"))
    return re.findall(rf"--{kind}-features=(\S+)", src)


def test_one_enable_features_flag():
    assert len(_flags("enable")) == 1, (
        "only the last --enable-features counts — merge them, comma-joined")


def test_one_disable_features_flag():
    assert len(_flags("disable")) == 1, (
        "only the last --disable-features counts — merge them, comma-joined")


def test_previously_dropped_features_are_still_requested():
    """The three that were silently inert before the merge."""
    disabled = _flags("disable")[0].split(",")
    for feature in ("TranslateUI", "IsolateOrigins", "site-per-process",
                    "MediaRouter", "InfiniteSessionRestore"):
        assert feature in disabled, f"{feature} lost while merging"


def test_enable_features_kept_both():
    enabled = _flags("enable")[0].split(",")
    assert "WebUIDarkMode" in enabled and "OverlayScrollbar" in enabled


def test_xinit_session_is_not_inline():
    """ui.sh must start the X session from a file, not `bash -c '<script>'`.

    bash reports a killed background job by echoing its command text, so an
    inline session script was dumped into the journal on every beo-ui stop —
    its log() strings ("Chromium failed to create a window...") then showed
    up as false failures in log greps.
    """
    xinit_lines = [line for line in (SERVICES / "ui.sh").read_text().splitlines()
                   if line.lstrip().startswith("xinit ")]
    assert xinit_lines == ['xinit /bin/bash "$SCRIPT_DIR/ui-session.sh" -- :0 vt7 &']
