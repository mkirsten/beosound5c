"""Central registry of inter-service HTTP endpoints.

Every BeoSound 5c service listens on a fixed localhost port.  Historically
these URLs were hardcoded as string literals in ~10 files, which produced
the family of "wrong port", "rename broke a caller", and local/remote
detection bugs (commits 121cf20, 16e17a8).  This module is the single
place that knows port numbers and path shapes.

All helpers return plain strings so callers can drop them into aiohttp
session calls unchanged.  Test suites can monkeypatch ``_BASE`` if they
need to point callers at a fake server.

If you need a new URL, add it here rather than inlining a literal — the
grep-lint baseline (tests/unit/python/test_lint_baseline.py) fails CI on
any new ``localhost:87xx`` string outside this module.
"""

from __future__ import annotations

# ── Fixed service ports ────────────────────────────────────────────────
PLAYER_PORT = 8766   # beo-player-* (exactly one active per device)
INPUT_PORT = 8767    # beo-input (hardware HID + LED + webhook)
ROUTER_PORT = 8770   # beo-router
SPOTIFY_PORT = 8771  # beo-source-spotify (also canvas endpoint)
RADIO_PORT = 8779    # beo-source-radio

_BASE = "http://localhost"


# ── URL builders ───────────────────────────────────────────────────────
def player_url(path: str) -> str:
    """Return ``http://localhost:8766<path>``.  ``path`` must start with ``/``."""
    return f"{_BASE}:{PLAYER_PORT}{path}"


def router_url(path: str) -> str:
    """Return ``http://localhost:8770<path>``.  ``path`` must start with ``/``."""
    return f"{_BASE}:{ROUTER_PORT}{path}"


def input_url(path: str) -> str:
    """Return ``http://localhost:8767<path>``.  ``path`` must start with ``/``."""
    return f"{_BASE}:{INPUT_PORT}{path}"


def source_url(port: int, path: str) -> str:
    """Return a URL for a specific source service port."""
    return f"{_BASE}:{port}{path}"


# ── Stable convenience constants ───────────────────────────────────────
# Only URLs used in multiple files are exported as constants; one-off
# URLs should use the builders above so the path lives at the call site.

INPUT_WEBHOOK = f"{_BASE}:{INPUT_PORT}/webhook"
INPUT_LED_PULSE = f"{_BASE}:{INPUT_PORT}/led?mode=pulse"

_PLAYER_BASE = f"{_BASE}:{PLAYER_PORT}/player"
PLAYER_STATE = f"{_PLAYER_BASE}/state"
PLAYER_MEDIA = f"{_PLAYER_BASE}/media"
PLAYER_STOP = f"{_PLAYER_BASE}/stop"
PLAYER_ANNOUNCE = f"{_PLAYER_BASE}/announce"
PLAYER_TOGGLE = f"{_PLAYER_BASE}/toggle"
PLAYER_NEXT = f"{_PLAYER_BASE}/next"
PLAYER_PREV = f"{_PLAYER_BASE}/prev"
PLAYER_JOIN = f"{_PLAYER_BASE}/join"
PLAYER_UNJOIN = f"{_PLAYER_BASE}/unjoin"
PLAYER_TRACK_URI = f"{_PLAYER_BASE}/track_uri"
PLAYER_PLAY_FROM_QUEUE = f"{_PLAYER_BASE}/play_from_queue"
PLAYER_COMMAND = _PLAYER_BASE  # legacy base for player_url() callers

_ROUTER_BASE = f"{_BASE}:{ROUTER_PORT}/router"
ROUTER_EVENT = f"{_ROUTER_BASE}/event"
ROUTER_SOURCE = f"{_ROUTER_BASE}/source"
ROUTER_MEDIA = f"{_ROUTER_BASE}/media"
ROUTER_BROADCAST = f"{_ROUTER_BASE}/broadcast"
ROUTER_RESYNC = f"{_ROUTER_BASE}/resync"
ROUTER_VOLUME_REPORT = f"{_ROUTER_BASE}/volume/report"
ROUTER_PLAYBACK_OVERRIDE = f"{_ROUTER_BASE}/playback_override"
ROUTER_OUTPUT_ON = f"{_ROUTER_BASE}/output/on"
ROUTER_OUTPUT_OFF = f"{_ROUTER_BASE}/output/off"
ROUTER_TOUCH = f"{_ROUTER_BASE}/touch"
ROUTER_STATUS = f"{_ROUTER_BASE}/status"

# beo-masterlink mixer HTTP API (only present on devices with a PC2 card)
MIXER_ML_STANDBY = "http://localhost:8768/ml/standby"


# ── Per-source command endpoints used by lydbro ────────────────────────
SPOTIFY_COMMAND = f"{_BASE}:{SPOTIFY_PORT}/command"
RADIO_COMMAND = f"{_BASE}:{RADIO_PORT}/command"


def spotify_canvas_url(track_id: str) -> str:
    """Canvas endpoint on the Spotify source service."""
    return f"{_BASE}:{SPOTIFY_PORT}/canvas?track_id={track_id}"
