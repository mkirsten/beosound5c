"""
Spotify token management — the ONE place for token refresh.

Two interfaces:
  - get_access_token()  — sync, for scripts (fetch.py)
  - SpotifyAuth         — async, for the long-running service (service.py)

Both use pkce.refresh_access_token() and tokens.load/save_tokens() internally.
"""

import asyncio
import json
import logging
import os
import sys
import time
import urllib.error

# Sibling imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pkce import refresh_access_token
from tokens import load_tokens, save_tokens

log = logging.getLogger('beo-spotify')


def get_access_token():
    """Get a Spotify access token (sync). For standalone fetch.py runs.

    Uses the PKCE token store. Handles refresh token rotation automatically.
    The service passes --access-token instead; this exists for manual/cron use.
    """
    tokens = load_tokens()
    if not tokens or not tokens.get('client_id') or not tokens.get('refresh_token'):
        raise ValueError(
            "No Spotify credentials found. Use the /setup page to connect."
        )

    client_id = tokens['client_id']
    refresh_token = tokens['refresh_token']

    result = refresh_access_token(client_id, refresh_token)

    # Persist rotated refresh token if provided
    new_rt = result.get('refresh_token')
    if new_rt and new_rt != refresh_token:
        save_tokens(client_id, new_rt)

    return result['access_token']


class SpotifyAuth:
    """Manages Spotify access tokens with automatic refresh (async).

    For use by the long-running Spotify service. Adds in-memory caching
    and revocation detection on top of the shared pkce/tokens modules.
    """

    def __init__(self):
        self._access_token = None
        self._token_expiry = 0
        self._client_id = None
        self._refresh_token = None
        self.revoked = False

    def load(self):
        """Load credentials from token store. Returns True if valid credentials found."""
        tokens = load_tokens()
        if tokens and tokens.get('client_id') and tokens.get('refresh_token'):
            self._client_id = tokens['client_id']
            self._refresh_token = tokens['refresh_token']
            log.info("Spotify credentials loaded (client_id: %s...)", self._client_id[:8])
            return True
        if tokens is not None:
            log.info("Token file exists but incomplete — waiting for setup")
        else:
            log.warning("No Spotify tokens found — use the /setup page to connect")
        return False

    def set_credentials(self, client_id, refresh_token, access_token=None, expires_in=3600):
        """Set credentials directly (used after OAuth callback)."""
        self._client_id = client_id
        self._refresh_token = refresh_token
        self._access_token = access_token
        self._token_expiry = time.monotonic() + expires_in - 300 if access_token else 0
        self.revoked = False

    def clear(self):
        """Clear all credentials (used on logout)."""
        self._client_id = None
        self._refresh_token = None
        self._access_token = None
        self._token_expiry = 0

    async def get_token(self):
        """Get a valid access token, refreshing if needed."""
        if self._access_token and time.monotonic() < self._token_expiry:
            return self._access_token
        return await self._refresh()

    async def _refresh(self):
        """Refresh the access token via PKCE."""
        if not self._client_id or not self._refresh_token:
            raise RuntimeError("No Spotify credentials")

        loop = asyncio.get_running_loop()

        try:
            result = await loop.run_in_executor(
                None, refresh_access_token, self._client_id, self._refresh_token)
        except urllib.error.HTTPError as e:
            if e.code == 400:
                self._mark_revoked(e)
            raise

        self._access_token = result['access_token']
        self._token_expiry = time.monotonic() + result.get('expires_in', 3600) - 300

        # Persist rotated refresh token
        new_rt = result.get('refresh_token')
        if new_rt and new_rt != self._refresh_token:
            self._refresh_token = new_rt
            await loop.run_in_executor(
                None, save_tokens, self._client_id, new_rt)
            log.info("Refresh token rotated")

        log.info("Access token refreshed (expires in %ds)", result.get('expires_in', 0))
        return self._access_token

    def _mark_revoked(self, exc):
        """Flag that the refresh token has been revoked by Spotify."""
        try:
            body = json.loads(exc.read().decode())
            error = body.get('error', '')
        except Exception:
            error = ''
        if error == 'invalid_grant':
            self.revoked = True
            log.error("Spotify refresh token revoked — re-authentication required")
        else:
            log.warning("Token refresh failed (400): %s", error)

    @property
    def is_configured(self):
        return bool(self._client_id and self._refresh_token)
