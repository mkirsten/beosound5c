"""
Apple Music authentication — developer token + user token management.

Developer token: pre-built JWT from Music Assistant (Apache 2.0 licensed).
  - No signing or Apple Developer account required.
  - Must be refreshed every ~6 months from MA's latest release.
  - See DEVELOPER_TOKEN below for extraction instructions.

User token: obtained via MusicKit JS authorization in the browser.
  - Stored in tokens.json, loaded at startup.
  - Expires after ~180 days; service detects 401 and sets revoked=True.
"""

import base64
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apple_music_tokens import load_tokens

log = logging.getLogger('beo-source-apple-music')

# ── Developer Token ──
# Extracted from Music Assistant (Apache 2.0) — music_assistant/helpers/app_vars.py, app_var(8)
# Last updated: 2026-02-23
# Expires: 2026-04-23
# To refresh: check https://github.com/music-assistant/server for latest release,
#   find music_assistant/helpers/app_vars.py, decode app_var(8) and paste here.
DEVELOPER_TOKEN = (
    "eyJhbGciOiJFUzI1NiIsImtpZCI6IkFSRzJSN0xEOTkiLCJ0eXAiOiJKV1QifQ."
    "eyJpc3MiOiJHVVlGQUs4REM2IiwiZXhwIjoxNzc2OTExNzI1LCJpYXQiOjE3NjExMzQ3MjV9."
    "AS3xyKE5NnQh6EmKJD2MmFk4JLX-mxmBnfi3_4Kl2S0cgP5wn4JCtMUuQeNs2xnc1fWX0M3-T0FVX6NJMbtcdw"
)


def _resolve_developer_token():
    """Return the developer token, preferring a user-supplied override.

    ``APPLE_MUSIC_DEV_TOKEN`` (secrets.env) lets a device get a fresh
    token without a software update when the bundled one expires.
    """
    return os.environ.get('APPLE_MUSIC_DEV_TOKEN', '').strip() or DEVELOPER_TOKEN


def developer_token_expiry(token=None):
    """Return the JWT ``exp`` epoch of the developer token, or None if
    it can't be decoded."""
    token = token or _resolve_developer_token()
    try:
        seg = token.split('.')[1]
        seg += '=' * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg)).get('exp')
    except Exception:
        return None


class AppleMusicAuth:
    """Manages Apple Music tokens for the long-running service."""

    def __init__(self):
        self._user_token = None
        self._storefront = None
        self.revoked = False
        # Check the bundled/override developer token's exp up front — an
        # expired developer token 401s every request, which looks exactly
        # like user-token revocation but is NOT fixable by re-auth.
        exp = developer_token_expiry()
        self.developer_token_valid = bool(exp and exp > time.time())
        if not self.developer_token_valid:
            log.error(
                "Apple Music developer token is EXPIRED (exp=%s). Re-auth "
                "will NOT fix this. Provide a fresh token via "
                "APPLE_MUSIC_DEV_TOKEN in /etc/beosound5c/secrets.env, or "
                "update BeoSound 5c.", exp)
        elif exp and exp - time.time() < 30 * 86400:
            log.warning("Apple Music developer token expires in %d days — "
                        "update BeoSound 5c or set APPLE_MUSIC_DEV_TOKEN "
                        "before it lapses.", int((exp - time.time()) / 86400))

    def load(self):
        """Load user token from token store. Returns True if valid credentials found."""
        tokens = load_tokens()
        if tokens and tokens.get('user_token'):
            self._user_token = tokens['user_token']
            self._storefront = tokens.get('storefront', 'us')
            log.info("Apple Music user token loaded (storefront: %s)", self._storefront)
            return True
        if tokens is not None:
            log.info("Token file exists but incomplete — waiting for setup")
        else:
            log.info("No Apple Music tokens found — use the /setup page to connect")
        return False

    def set_credentials(self, user_token, storefront='us'):
        """Set credentials directly (used after MusicKit JS callback)."""
        self._user_token = user_token
        self._storefront = storefront
        self.revoked = False

    def clear(self):
        """Clear all user credentials."""
        self._user_token = None
        self._storefront = None

    def get_developer_token(self):
        """Return the developer token string (override-aware)."""
        return _resolve_developer_token()

    def get_user_token(self):
        """Return the stored user token."""
        return self._user_token

    @property
    def storefront(self):
        return self._storefront or 'us'

    @property
    def is_configured(self):
        return bool(self._user_token and self.developer_token_valid)
