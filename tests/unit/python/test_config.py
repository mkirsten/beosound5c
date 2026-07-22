"""Tests for services/lib/config.py — the config loader every service depends on."""

import json
import logging

import pytest

from lib.config import cfg, load_config, reload_config


# --- cfg() getter logic ---


class TestCfgGetter:
    def test_single_key(self, write_config):
        write_config({"device": "Church"})
        assert cfg("device") == "Church"

    def test_two_key_nested(self, write_config):
        write_config({"player": {"type": "sonos", "ip": "192.168.1.100"}})
        assert cfg("player", "ip") == "192.168.1.100"

    def test_default_when_key_missing(self, write_config):
        write_config({"device": "Church"})
        assert cfg("nonexistent", default="fallback") == "fallback"

    def test_default_when_nested_key_missing(self, write_config):
        write_config({"player": {"type": "sonos"}})
        assert cfg("player", "ip", default="1.2.3.4") == "1.2.3.4"

    def test_default_when_section_not_dict(self, write_config):
        """cfg("device", "sub") should return default when device is a string, not crash."""
        write_config({"device": "Church"})
        assert cfg("device", "sub", default="safe") == "safe"

    def test_returns_none_when_missing_no_default(self, write_config):
        write_config({})
        assert cfg("anything") is None

    def test_returns_none_nested_missing_no_default(self, write_config):
        write_config({"player": {"type": "sonos"}})
        assert cfg("player", "missing") is None

    def test_returns_whole_dict_for_section(self, write_config):
        menu = {"1": "spotify", "2": "scenes"}
        write_config({"menu": menu})
        assert cfg("menu") == menu

    def test_numeric_values(self, write_config):
        write_config({"volume": {"max": 70, "type": "sonos"}})
        assert cfg("volume", "max") == 70

    def test_boolean_values(self, write_config):
        write_config({"debug": True})
        assert cfg("debug") is True


# --- Loading behavior ---


class TestLoadBehavior:
    def test_loads_from_first_valid_path(self, tmp_path, monkeypatch):
        import lib.config as config_mod

        first = tmp_path / "first.json"
        second = tmp_path / "second.json"
        first.write_text(json.dumps({"device": "First"}))
        second.write_text(json.dumps({"device": "Second"}))
        monkeypatch.setattr(config_mod, "_SEARCH_PATHS", [str(first), str(second)])

        assert cfg("device") == "First"

    def test_skips_invalid_json(self, tmp_path, monkeypatch, caplog):
        import lib.config as config_mod

        bad = tmp_path / "bad.json"
        good = tmp_path / "good.json"
        bad.write_text("{invalid json")
        good.write_text(json.dumps({"device": "Good"}))
        monkeypatch.setattr(config_mod, "_SEARCH_PATHS", [str(bad), str(good)])

        with caplog.at_level(logging.ERROR):
            result = cfg("device")

        assert result == "Good"
        assert any("Invalid JSON" in r.message for r in caplog.records)

    def test_raises_when_no_files_found(self, tmp_path, monkeypatch):
        """Previous behaviour silently fell back to ``{}`` — that masked
        real install / deploy problems.  Fail loud instead."""
        import lib.config as config_mod
        from lib.config import ConfigError
        monkeypatch.setattr(config_mod, "_SEARCH_PATHS", [str(tmp_path / "nope.json")])
        with pytest.raises(ConfigError, match="No config.json found"):
            load_config()

    def test_raises_when_only_file_is_invalid_json(self, tmp_path, monkeypatch):
        import lib.config as config_mod
        from lib.config import ConfigError
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json")
        monkeypatch.setattr(config_mod, "_SEARCH_PATHS", [str(bad)])
        with pytest.raises(ConfigError, match="invalid JSON"):
            load_config()

    def test_raises_on_duplicate_source_button(self, write_config):
        """A single IR button mapped to two sources silently corrupts
        routing ("last one wins").  It should fail loud at startup."""
        from lib.config import ConfigError
        write_config({
            "device": "Church",
            "menu": {"1": "spotify", "2": "radio"},
            "spotify": {"source": "radio"},
            "radio": {"source": "radio"},
        })
        with pytest.raises(ConfigError, match="source button 'radio'"):
            load_config()

    def test_caches_after_first_load(self, write_config, config_file):
        write_config({"device": "First"})
        assert cfg("device") == "First"

        # Overwrite file — should still return cached value
        config_file.write_text(json.dumps({"device": "Second"}))
        assert cfg("device") == "First"

    def test_reload_forces_reread(self, write_config, config_file):
        write_config({"device": "First"})
        assert cfg("device") == "First"

        config_file.write_text(json.dumps({"device": "Second"}))
        reload_config()
        assert cfg("device") == "Second"


# --- Validation warnings ---


class TestValidation:
    def test_warns_on_missing_device(self, write_config, caplog):
        with caplog.at_level(logging.WARNING):
            write_config({"menu": {"1": "spotify"}})
            load_config()
        assert any("missing 'device'" in r.message for r in caplog.records)

    def test_warns_on_missing_menu(self, write_config, caplog):
        with caplog.at_level(logging.WARNING):
            write_config({"device": "Church"})
            load_config()
        assert any("missing 'menu'" in r.message for r in caplog.records)

    def test_warns_on_missing_webhook_url(self, write_config, caplog):
        with caplog.at_level(logging.WARNING):
            write_config({"device": "Church", "menu": {"1": "spotify"}})
            load_config()
        assert any("missing home_assistant.webhook_url" in r.message for r in caplog.records)

    def test_warns_on_unknown_volume_type(self, write_config, caplog):
        with caplog.at_level(logging.WARNING):
            write_config({"device": "Church", "menu": {"1": "spotify"},
                          "volume": {"type": "banana"}})
            load_config()
        assert any("unknown volume.type 'banana'" in r.message for r in caplog.records)

    def test_no_warning_for_valid_volume_types(self, write_config, caplog):
        for vtype in ("beolab5", "sonos", "bluesound", "heos", "powerlink",
                       "c4amp", "hdmi", "spdif", "rca"):
            with caplog.at_level(logging.WARNING):
                caplog.clear()
                write_config({"device": "X", "menu": {"1": "a"},
                              "home_assistant": {"webhook_url": "http://x"},
                              "volume": {"type": vtype}})
                load_config()
            assert not any("unknown volume.type" in r.message for r in caplog.records), \
                f"Unexpected warning for volume type '{vtype}'"

    def test_errors_on_news_without_api_key(self, write_config, caplog):
        with caplog.at_level(logging.ERROR):
            write_config({"device": "Church", "menu": {"5": "news"}})
            load_config()
        assert any("NEWS source in menu but no news.guardian_api_key" in r.message
                    for r in caplog.records)

    def test_news_dict_form_triggers_error(self, write_config, caplog):
        """Menu entries can be dicts with an 'id' field."""
        with caplog.at_level(logging.ERROR):
            write_config({"device": "Church",
                          "menu": {"5": {"id": "news", "label": "News"}}})
            load_config()
        assert any("NEWS source in menu but no news.guardian_api_key" in r.message
                    for r in caplog.records)

    def test_no_news_error_when_key_present(self, write_config, caplog):
        with caplog.at_level(logging.ERROR):
            write_config({"device": "Church",
                          "menu": {"5": "news"},
                          "news": {"guardian_api_key": "abc123"}})
            load_config()
        assert not any("NEWS source in menu" in r.message for r in caplog.records)
