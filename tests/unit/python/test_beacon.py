"""Tests for services/lib/beacon.py — UUID stability and beacon payload."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "services"))

from lib.beacon import _BEACON_NAMESPACE, _get_or_create_device_id, _build_payload


# ── UUID persistence ──────────────────────────────────────────────────────────

def test_uuid_created_on_first_call(tmp_path):
    """A UUID is generated and written when no device_id file exists."""
    result = _get_or_create_device_id(str(tmp_path))
    id_file = tmp_path / "device_id"
    assert id_file.exists()
    assert result == id_file.read_text().strip()


def test_uuid_is_valid_uuid(tmp_path):
    """Result is a valid UUID — version depends on whether a MAC is available."""
    result = _get_or_create_device_id(str(tmp_path))
    parsed = uuid.UUID(result)
    assert parsed.version in (4, 5)


def test_uuid_derived_from_mac_is_deterministic(tmp_path):
    """When a stable MAC is available, the same MAC produces the same UUID
    even across separate base paths (i.e. across reinstalls)."""
    mac = "b8:27:eb:12:34:56"
    expected = str(uuid.uuid5(_BEACON_NAMESPACE, mac))

    install_a = tmp_path / "install_a"
    install_b = tmp_path / "install_b"
    install_a.mkdir()
    install_b.mkdir()

    with patch("lib.beacon._read_stable_mac", return_value=mac):
        a = _get_or_create_device_id(str(install_a))
        b = _get_or_create_device_id(str(install_b))

    assert a == b == expected


def test_uuid_falls_back_to_uuid4_when_no_mac(tmp_path):
    """No stable MAC (e.g. dev mode on macOS) falls back to a random UUID4."""
    with patch("lib.beacon._read_stable_mac", return_value=None):
        result = _get_or_create_device_id(str(tmp_path))
    assert uuid.UUID(result).version == 4


def test_uuid_stable_across_calls(tmp_path):
    """Same UUID returned on every subsequent call — simulates reboots."""
    first = _get_or_create_device_id(str(tmp_path))
    second = _get_or_create_device_id(str(tmp_path))
    third = _get_or_create_device_id(str(tmp_path))
    assert first == second == third


def test_uuid_stable_when_file_pre_exists(tmp_path):
    """Pre-existing UUID is preserved verbatim — never overwritten by the
    MAC-derived value (keeps beacon history continuity for older deployments)."""
    known_id = "aaaaaaaa-bbbb-4ccc-dddd-eeeeeeeeeeee"
    (tmp_path / "device_id").write_text(known_id + "\n")
    with patch("lib.beacon._read_stable_mac", return_value="b8:27:eb:12:34:56"):
        result = _get_or_create_device_id(str(tmp_path))
    assert result == known_id


def test_uuid_survives_ota_exclude_list():
    """device_id is in _UPDATE_EXCLUDES so OTA rsync never clobbers it."""
    # Read the list directly from source rather than importing the full module
    # (input.py imports `hid` which isn't available in the test environment).
    source = (REPO_ROOT / "services" / "input.py").read_text()
    # Extract the _UPDATE_EXCLUDES list as text and check for the entry
    import ast, re
    m = re.search(r"_UPDATE_EXCLUDES\s*=\s*(\[.*?\])", source, re.DOTALL)
    assert m, "_UPDATE_EXCLUDES not found in input.py"
    excludes = ast.literal_eval(m.group(1))
    assert "device_id" in excludes, "device_id must be in _UPDATE_EXCLUDES"


def test_uuid_survives_deploy_ignore():
    """.deployignore lists device_id so deploy.sh --delete never removes it."""
    deployignore = REPO_ROOT / ".deployignore"
    if not deployignore.exists():
        pytest.skip(".deployignore not present in this repo")
    lines = deployignore.read_text().splitlines()
    assert "device_id" in lines, ".deployignore must contain 'device_id'"


def test_uuid_fallback_on_unwritable_dir(tmp_path):
    """Returns 'unknown' gracefully if the file can't be written — never raises."""
    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir(mode=0o555)
    result = _get_or_create_device_id(str(ro_dir))
    assert result == "unknown"


# ── Payload shape ─────────────────────────────────────────────────────────────

def test_payload_contains_required_keys(tmp_path):
    (tmp_path / "VERSION").write_text("v0.8.0\n")
    with patch("lib.beacon._get_or_create_device_id", return_value="test-uuid"), \
         patch("lib.config.load_config", return_value={
             "device": "Test", "player": {"type": "sonos"},
             "volume": {"type": "beolab5"}, "spotify": {},
         }):
        payload = _build_payload(str(tmp_path))

    assert payload["device_id"] == "test-uuid"
    assert payload["version"] == "v0.8.0"
    assert isinstance(payload["sources"], list)
    assert payload["player_type"] == "sonos"
    assert payload["volume_type"] == "beolab5"


def test_payload_sources_excludes_system_sections(tmp_path):
    (tmp_path / "VERSION").write_text("v0.8.0\n")
    config = {
        "device": "x", "menu": {}, "scenes": [], "player": {}, "volume": {},
        "home_assistant": {}, "transport": {}, "showing": {}, "join": {},
        "bluetooth": {}, "remote": {},
        "spotify": {"client_id": "abc"},
        "cd": {"device": "/dev/sr0"},
        "radio": {},
    }
    with patch("lib.beacon._get_or_create_device_id", return_value="x"), \
         patch("lib.config.load_config", return_value=config):
        payload = _build_payload(str(tmp_path))

    assert set(payload["sources"]) == {"spotify", "cd", "radio"}


def test_payload_version_fallback_when_no_file(tmp_path):
    with patch("lib.beacon._get_or_create_device_id", return_value="x"), \
         patch("lib.config.load_config", return_value={}):
        payload = _build_payload(str(tmp_path))
    assert payload["version"] == "unknown"
