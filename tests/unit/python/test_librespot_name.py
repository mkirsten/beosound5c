"""Tests for the Spotify Connect name go-librespot advertises.

go-librespot advertises its own `device_name` from its own config file, which
the installer writes once — before /etc/beosound5c/config.json even exists, so
it always landed on the literal "BeoSound 5c". Two units on one Spotify account
then shared a name and the second could not be paired at all.
reconcile-services.sh now fixes the name up from config.json on every install,
deploy and config save.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "services"))

from lib import librespot_config as lc  # noqa: E402

CONFIG_YML = """device_name: "BeoSound 5c"
device_type: speaker
audio_backend: pulseaudio
bitrate: 320
zeroconf_enabled: true
server:
  enabled: true
  port: 3678
"""


@pytest.fixture
def yml(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(CONFIG_YML)
    return path


def _device_name_lines(path):
    return [ln for ln in path.read_text().splitlines()
            if ln.startswith("device_name:")]


# ── Name derivation ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("device,expected", [
    ("Kitchen", "BeoSound 5c Kitchen"),
    ("Vardagsrum", "BeoSound 5c Vardagsrum"),
    ("  Office  ", "BeoSound 5c Office"),
])
def test_device_name_is_prefixed(device, expected):
    assert lc.spotify_connect_name(device) == expected


@pytest.mark.parametrize("device", [
    "BeoSound 5c",           # config/default.json ships this
    "BeoSound 5c Kitchen",   # owner already spelled it out
    "beosound 5c loft",      # case shouldn't matter
])
def test_a_name_that_already_says_beosound_is_left_alone(device):
    assert lc.spotify_connect_name(device) == device.strip()


@pytest.mark.parametrize("device", ["", "   ", None])
def test_an_empty_device_name_falls_back(device):
    assert lc.spotify_connect_name(device) == "BeoSound 5c"


def test_distinct_devices_get_distinct_names():
    """The whole point: two units must never advertise the same name."""
    names = {lc.spotify_connect_name(d)
             for d in ("Kitchen", "Church", "Office")}
    assert len(names) == 3


# ── Writing ───────────────────────────────────────────────────────────────────

def test_name_is_replaced(yml):
    assert lc.set_device_name(str(yml), "BeoSound 5c Kitchen") is True
    assert _device_name_lines(yml) == ['device_name: "BeoSound 5c Kitchen"']


def test_rest_of_the_config_survives(yml):
    lc.set_device_name(str(yml), "BeoSound 5c Kitchen")
    text = yml.read_text()
    for line in ("device_type: speaker", "audio_backend: pulseaudio",
                 "bitrate: 320", "zeroconf_enabled: true", "  port: 3678"):
        assert line in text, f"{line!r} must survive the rename"


@pytest.mark.parametrize("name,expected_line", [
    ('Kök "andra våningen"', r'device_name: "Kök \"andra våningen\""'),
    ("back\\slash", r'device_name: "back\\slash"'),
    ("Vardagsrum – övre", 'device_name: "Vardagsrum – övre"'),
])
def test_special_characters_are_escaped(yml, name, expected_line):
    """go-librespot parses YAML — an unescaped quote breaks its whole config."""
    lc.set_device_name(str(yml), name)
    assert _device_name_lines(yml) == [expected_line]


def test_missing_file_is_not_created(tmp_path):
    """No config.yml means no local player — nothing to rename."""
    path = tmp_path / "config.yml"
    assert lc.set_device_name(str(path), "BeoSound 5c Kitchen") is False
    assert not path.exists()


def test_config_without_a_device_name_line_is_left_alone(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("device_type: speaker\nbitrate: 320\n")
    assert lc.set_device_name(str(path), "BeoSound 5c Kitchen") is False
    assert path.read_text() == "device_type: speaker\nbitrate: 320\n"


def test_unchanged_name_does_not_rewrite(yml):
    with patch("os.replace", side_effect=AssertionError("must not rewrite")):
        assert lc.set_device_name(str(yml), "BeoSound 5c") is False


# ── Atomicity ─────────────────────────────────────────────────────────────────

def test_original_survives_a_failed_write(yml, tmp_path):
    with patch("os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            lc.set_device_name(str(yml), "BeoSound 5c Kitchen")

    assert yml.read_text() == CONFIG_YML, "a failed write must not truncate"
    leftovers = [p.name for p in tmp_path.iterdir()
                 if p.name.startswith(".config.yml.")]
    assert leftovers == [], "failed write must clean up its temp file"


def test_no_temp_file_left_behind(yml, tmp_path):
    lc.set_device_name(str(yml), "BeoSound 5c Kitchen")
    leftovers = [p.name for p in tmp_path.iterdir()
                 if p.name.startswith(".config.yml.")]
    assert leftovers == []


# ── Reading the device name out of config.json ────────────────────────────────

def test_sync_reads_the_device_name(tmp_path, yml):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"device": "Kitchen", "player": {"type": "local"}}))

    assert lc.sync_from_config(str(cfg), str(yml)) == "BeoSound 5c Kitchen"
    assert _device_name_lines(yml) == ['device_name: "BeoSound 5c Kitchen"']


def test_sync_reports_no_change_when_already_correct(tmp_path, yml):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"device": "BeoSound 5c"}))
    assert lc.sync_from_config(str(cfg), str(yml)) is None


def test_sync_is_idempotent(tmp_path, yml):
    """reconcile runs on every save — a second pass must be a no-op."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"device": "Kitchen"}))

    lc.sync_from_config(str(cfg), str(yml))
    after_first = yml.read_text()
    assert lc.sync_from_config(str(cfg), str(yml)) is None
    assert yml.read_text() == after_first


def test_a_rename_is_not_re_prefixed(tmp_path, yml):
    """Kitchen -> Loft must not become 'BeoSound 5c BeoSound 5c Loft'."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"device": "Kitchen"}))
    lc.sync_from_config(str(cfg), str(yml))

    cfg.write_text(json.dumps({"device": "Loft"}))
    assert lc.sync_from_config(str(cfg), str(yml)) == "BeoSound 5c Loft"
    assert _device_name_lines(yml) == ['device_name: "BeoSound 5c Loft"']


# ── Wiring ────────────────────────────────────────────────────────────────────

def test_reconcile_syncs_the_name_before_restarting_services():
    """The restart is what carries the new name to Spotify — order matters."""
    text = (REPO_ROOT / "services" / "system" / "reconcile-services.sh").read_text()
    sync_at = text.index("librespot_config.py")
    restart_at = text.index("systemctl try-restart")
    assert sync_at < restart_at, "name must be written before the restart"


def test_installer_does_not_guess_the_name():
    """It runs before config.json exists — the old lookup was dead code."""
    text = (REPO_ROOT / "install" / "modules" / "librespot.sh").read_text()
    assert "CFG_NAME" not in text
