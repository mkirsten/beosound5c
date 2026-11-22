"""HOME view (/ha/panel): the two setup gaps answer differently.

No entities configured and no Home Assistant token used to both come back
as an empty list, so the view told someone with a missing token to add
entities. Neither case should reach Home Assistant.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import make_mocked_request

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "services"))
sys.modules.setdefault("hid", type(sys)("hid"))

import input as beo_input  # noqa: E402  (services/input.py)


def _call(panel, token):
    def fake_cfg(*keys, default=None):
        return panel if keys == ("home_assistant", "panel") else default

    async def no_session():
        raise AssertionError("must not contact Home Assistant")

    with patch.object(beo_input, "cfg", fake_cfg), \
            patch.object(beo_input, "get_http_session", no_session), \
            patch.dict("os.environ", {"HA_TOKEN": token}):
        resp = asyncio.run(beo_input.handle_ha_panel(
            make_mocked_request("GET", "/ha/panel")))
    return resp.status, json.loads(resp.body)


def test_no_entities_is_an_empty_list():
    assert _call([], "secret") == (200, [])


def test_missing_token_says_so():
    assert _call(["sensor.outdoor"], "") == (503, {"error": "no_token"})
