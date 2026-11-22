"""A multi-disc CD must show only the inserted disc's tracks.

Inserting CD3 of "Kristina från Duvemåla: Den kompletta utgåvan" listed 39
tracks — every medium in the release flattened together. Worse, MusicBrainz
numbers tracks *per medium*, so the list contained three tracks numbered 1,
three numbered 2, and so on, which makes ``play_track`` ambiguous as well as
the browser wrong. Found on Mammuten, Oct 12 2026.

``select_medium`` picks the one medium matching the disc in the drive, and
``medium_tracks`` numbers from that medium alone.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest


def _load_cd_module():
    """Import ``sources.cd`` with pyudev stubbed, leaving sys.modules as found."""
    added = []
    if "pyudev" not in sys.modules:
        stub = types.ModuleType("pyudev")
        stub.Context = object
        stub.Monitor = object
        stub.MonitorObserver = object
        sys.modules["pyudev"] = stub
        added.append("pyudev")
    before = set(sys.modules)
    try:
        return importlib.import_module("sources.cd")
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"CD service not importable here: {exc}")
    finally:
        for name in set(sys.modules) - before | set(added):
            sys.modules.pop(name, None)


cd = _load_cd_module()

DISC3_ID = "disc-three-id"


def _medium(position, n_tracks, disc_ids=(), title_prefix="T"):
    return {
        "position": str(position),
        "disc-list": [{"id": d} for d in disc_ids],
        "track-list": [
            {
                "position": str(i),
                "recording": {"title": f"{title_prefix}{position}-{i}",
                              "length": str(60000 * i)},
            }
            for i in range(1, n_tracks + 1)
        ],
    }


# CD1 = 19 tracks, CD2 = 21, CD3 = 18 (the one in the drive)
KRISTINA = {
    "title": "Kristina från Duvemåla: Den kompletta utgåvan",
    "medium-list": [
        _medium(1, 19, ["disc-one-id"]),
        _medium(2, 21, ["disc-two-id"]),
        _medium(3, 18, [DISC3_ID]),
    ],
}


def test_selects_disc_by_discid():
    """The exact bug: CD3 must yield 18 tracks numbered 1..18, not 39."""
    medium = cd.select_medium(KRISTINA, DISC3_ID, 18)
    assert medium is not None
    assert medium["position"] == "3"

    tracks = cd.medium_tracks(medium)
    assert len(tracks) == 18
    assert [t["num"] for t in tracks] == list(range(1, 19))
    # no duplicate numbers — what made play_track ambiguous
    assert len({t["num"] for t in tracks}) == 18
    assert tracks[0]["title"] == "T3-1"


def test_falls_back_to_unique_track_count():
    """No discids in the response (older include set) → match the TOC length."""
    stripped = {
        "medium-list": [
            {k: v for k, v in m.items() if k != "disc-list"}
            for m in KRISTINA["medium-list"]
        ]
    }
    medium = cd.select_medium(stripped, DISC3_ID, 18)
    assert medium["position"] == "3"
    assert len(cd.medium_tracks(medium)) == 18


def test_ambiguous_track_count_picks_one_disc_not_all():
    """Two discs of equal length: still return a single medium, never flattened."""
    release = {"medium-list": [_medium(1, 12), _medium(2, 12)]}
    medium = cd.select_medium(release, "", 12)
    assert medium is not None
    assert len(cd.medium_tracks(medium)) == 12


def test_single_disc_release_unaffected():
    """The common case must keep working with no disc ID and no TOC count."""
    release = {"medium-list": [_medium(1, 15, ["only-disc"])]}
    assert cd.select_medium(release, "", 0)["position"] == "1"
    assert cd.select_medium(release, "only-disc", 15)["position"] == "1"
    assert len(cd.medium_tracks(release["medium-list"][0])) == 15


def test_unidentifiable_multidisc_returns_none():
    """Caller falls back to a TOC-numbered list rather than a wrong disc."""
    release = {"medium-list": [_medium(1, 19), _medium(2, 21)]}
    assert cd.select_medium(release, "no-match", 18) is None


def test_no_media():
    assert cd.select_medium({}, "x", 5) is None
    assert cd.select_medium({"medium-list": []}, "x", 5) is None


def test_medium_tracks_handles_missing_fields():
    """Durations and titles are optional in MusicBrainz responses."""
    tracks = cd.medium_tracks({"track-list": [
        {"position": "1", "recording": {}},
        {"position": "2"},
    ]})
    assert tracks[0]["title"] == "Track 1"
    assert tracks[0]["duration"] == "0:00"
    assert tracks[1]["num"] == 2
