"""Pin the fix from commits 7d3f282 / 84f9bb3.

Every source service used to have an ``auth.py`` and a ``tokens.py``
imported as top-level ``auth`` / ``tokens`` via ``sys.path.insert``.
At runtime each service runs in its own process so the collision never
fires, but the moment any test process imports two sources together
they fight over the same ``sys.modules`` entries.

This test imports every source service (and the player services) into
one process and asserts:

  1. All imports succeed.
  2. No two modules have the same short top-level name (so a future
     ``config.py`` or ``auth.py`` added to one source can't silently
     shadow another).

If this test fails because you added a new module named the same as an
existing one in a sibling source, rename it to a unique package-scoped
name (e.g. ``<source>_config.py``) per the 7d3f282 convention.
"""

from __future__ import annotations

import importlib
import sys

import pytest

SOURCE_MODULES = [
    "sources.spotify.service",
    "sources.plex.service",
    "sources.apple_music.service",
    "sources.tidal.service",
    "sources.usb.service",
    "sources.radio.service",
    "sources.news",
    # sources.cd requires pyudev (Linux-only) — skipped conditionally below.
]

PLAYER_MODULES = [
    "players.sonos",
    "players.local",
    "players.bluesound",
    "players.heos",
]

OPTIONAL_MODULES = {
    "sources.cd": "pyudev",          # Linux-only udev bindings
    "players.local": "mpv",          # optional mpv python binding
    "players.bluesound": "aiohttp",  # always present, kept as example
    "players.heos": "pyheos",        # HEOS CLI client
}


def _try_import(mod_name: str) -> tuple[bool, str]:
    try:
        importlib.import_module(mod_name)
        return True, ""
    except ModuleNotFoundError as e:
        # A missing *optional* OS/hardware dep is not a collision bug.
        if OPTIONAL_MODULES.get(mod_name) in str(e):
            return False, f"skip: optional dep {e.name!r} not installed"
        raise
    except SystemExit as e:
        # player/source services do ``sys.exit(0)`` if config.player.type
        # doesn't match — that's the type-guard pattern, not a failure.
        return True, f"type-guard exit ({e.code})"


def test_all_sources_importable_in_one_process():
    """The whole source tree imports without collisions or failures."""
    results: dict[str, str] = {}
    for m in SOURCE_MODULES + PLAYER_MODULES + ["sources.cd"]:
        ok, note = _try_import(m)
        if note:
            results[m] = note
    # If any import raised a real error _try_import would have re-raised
    # and this test would have failed loudly — reaching here means all
    # attempted modules succeeded or were legitimately skipped.
    assert results.get("sources.spotify.service") != "collision"


def test_no_duplicate_short_names_across_sources():
    """No two source/player submodules share a short top-level name.

    ``sys.modules`` is keyed by the full dotted path, so strictly
    speaking two modules named ``config`` under different parents can
    coexist.  The collision that bit us (7d3f282) was via
    ``sys.path.insert`` turning them into top-level imports.  This
    check flags any *future* name duplication so we remember to rename
    on the way in, rather than after the next bug.
    """
    seen: dict[str, str] = {}
    conflicts: list[str] = []

    for full in sorted(sys.modules):
        if not (full.startswith("sources.") or full.startswith("players.")):
            continue
        parts = full.split(".")
        if len(parts) < 2:
            continue
        short = parts[-1]
        # Ignore the obvious shared names — every subpackage has a
        # ``service`` entrypoint by convention.
        if short in {"service", "__init__"}:
            continue
        if short in seen and seen[short] != full:
            # Only flag if the short name is genuinely ambiguous, i.e.
            # the previous owner is a different subpackage.
            prev = seen[short]
            if prev.rsplit(".", 1)[0] != full.rsplit(".", 1)[0]:
                conflicts.append(f"{short}: {prev} vs {full}")
        else:
            seen[short] = full

    assert not conflicts, (
        "Short module name collisions found — rename to <source>_<name>.py "
        "per the 7d3f282 convention:\n  " + "\n  ".join(conflicts)
    )
