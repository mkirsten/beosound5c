"""Tests for _device_slug() from services/lib/transport.py — pure function, no I/O."""

import re

from lib.transport import _device_slug


class TestDeviceSlug:
    def test_simple_lowercase(self):
        assert _device_slug("Church") == "church"

    def test_spaces_to_underscores(self):
        assert _device_slug("Living Room") == "living_room"

    def test_mqtt_illegal_chars_stripped(self):
        """MQTT topic segments cannot contain /, #, or +."""
        assert _device_slug("a/b#c+d") == "a_b_c_d"

    def test_multiple_underscores_collapsed(self):
        assert _device_slug("a   b") == "a_b"

    def test_leading_trailing_whitespace(self):
        assert _device_slug("  Church  ") == "church"

    def test_empty_string_returns_default(self):
        assert _device_slug("") == "default"

    def test_only_special_chars_returns_default(self):
        assert _device_slug("/##+") == "default"

    def test_already_valid_slug(self):
        assert _device_slug("kitchen") == "kitchen"

    def test_numbers_preserved(self):
        assert _device_slug("room2") == "room2"

    def test_mixed_case_and_symbols(self):
        assert _device_slug("My-Room.3") == "my_room_3"

    def test_no_mqtt_illegal_chars_in_output(self):
        """Property check: no /, #, or + should ever appear in output."""
        test_inputs = [
            "Church", "Living Room", "a/b#c+d", "", "/##+",
            "My/Room#3+extra", "  spaces  ", "under_score",
        ]
        for name in test_inputs:
            slug = _device_slug(name)
            assert not re.search(r"[/#+]", slug), \
                f"Slug '{slug}' from '{name}' contains MQTT illegal chars"

    def test_no_leading_trailing_underscores(self):
        assert _device_slug("_hello_") == "hello"

    def test_unicode_replaced(self):
        """Non-ASCII chars are replaced with underscore."""
        slug = _device_slug("Küche")
        assert slug == "k_che"
