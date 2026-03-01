"""Shared fixtures for BeoSound 5c Python unit tests."""

import json
import sys
import os
from pathlib import Path

import pytest

# Add services/ to sys.path so `from lib.config import cfg` works
SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))


@pytest.fixture(autouse=True)
def _reset_config_cache():
    """Reset the config module's cache before each test."""
    import lib.config as config_mod
    config_mod._config = None
    yield
    config_mod._config = None


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Provide a temp config file path and patch _SEARCH_PATHS to use it.

    Returns the Path object — write JSON to it with write_text() or use
    the write_config fixture for convenience.
    """
    import lib.config as config_mod

    path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "_SEARCH_PATHS", [str(path)])
    return path


@pytest.fixture
def write_config(config_file):
    """Write a dict as JSON to the temp config file.

    Usage:
        def test_something(write_config):
            write_config({"device": "Church", "player": {"type": "sonos"}})
            assert cfg("device") == "Church"
    """
    import lib.config as config_mod

    def _write(data: dict):
        config_file.write_text(json.dumps(data))
        config_mod._config = None  # force re-read
        return config_file

    return _write


@pytest.fixture
def mock_config(monkeypatch):
    """Directly set the config dict without file I/O.

    Usage:
        def test_something(mock_config):
            mock_config({"device": "Kitchen"})
            assert cfg("device") == "Kitchen"
    """
    import lib.config as config_mod

    def _mock(data: dict):
        monkeypatch.setattr(config_mod, "_config", data)

    return _mock
