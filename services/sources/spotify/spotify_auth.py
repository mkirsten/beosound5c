"""
Spotify token management — the ONE place for token refresh.

Three interfaces:
  - get_access_token()     — sync, for scripts (fetch.py)
  - SpotifyAuth            — async, for the long-running service (token master)
  - RemoteSpotifyAuth      — async, fetches token from a master device

Both SpotifyAuth and RemoteSpotifyAuth expose the same get_token() interface.
"""

import asyncio
import json
import logging
import os
import sys
import time
import urllib.error

import aiohttp

# Sibling imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pkce import refresh_access_token
from spotify_tokens import (
    delete_tokens,
    load_tokens,
    refresh_lock,
    save_tokens,
)

log = logging.getLogger('beo-source-spotify')

# Spotify expires refresh tokens 6 months after the user's ORIGINAL
# authorization (announced 2026-06-18; enforced for existing apps from
# 2026-07-20).  Rotation does NOT extend the window — only a full
# re-auth does.  Warn well before the cliff so the user can re-auth
# at their convenience instead of finding Spotify dead.
REFRESH_TOKEN_TTL = 180 * 86400
REAUTH_WARN_AFTER = 150 * 86400


class SpotifyReauthRequired(Exception):
    """The stored refresh token is expired/revoked (``invalid_grant``).

    Per Spotify's guidance the token has been discarded — do not retry;
    the user must re-authenticate via the /setup page.
    """


def missing_scopes(granted, required):
    """Return scopes in ``required`` that aren't in ``granted``.

    Both args are space-separated strings (Spotify's wire format) or None.
    A missing/empty granted set returns the full required set.  Treats
    scope sets case-insensitively per RFC 6749 (Spotify's are lowercase).
    """
    granted_set = set((granted or "").lower().split())
    required_set = set((required or "").lower().split())
    return sorted(required_set - granted_set)


def _read_oauth_error(exc):
    """Extract the OAuth ``error`` field from an HTTPError body, or ''.

    Consumes the response body — callers must not read it again.
    """
    try:
        return json.loads(exc.read().decode()).get('error', '')
    except Exception:
        return ''


def _refresh_under_file_lock(config_client_id=None, known_refresh_token=None):
    """Sync refresh path, serialised across processes via ``refresh_lock``.

    Shared by :func:`get_access_token` (sync scripts) and
    :meth:`SpotifyAuth._refresh` (long-running service, called in an
    executor).  PKCE rotates the refresh token on every refresh — two
    concurrent refreshers would race, and the loser's next attempt
    hits ``400 invalid_grant`` and the user has to re-authenticate.

    See commit c45a1cf.

    Inside the lock we reload tokens from disk first: if another
    process already refreshed, that rotation is visible and we pick
    up the new refresh_token instead of re-submitting the stale one.
    Returns ``(client_id, new_refresh_token, refresh_result)``.
    """
    with refresh_lock():
        tokens = load_tokens()
        if not tokens or not tokens.get('client_id') or not tokens.get('refresh_token'):
            raise ValueError(
                "No Spotify credentials found. Use the /setup page to connect."
            )

        client_id = tokens['client_id']
        if config_client_id and client_id != config_client_id:
            delete_tokens()
            raise ValueError(
                f"Spotify client_id changed ({client_id[:8]}... → "
                f"{config_client_id[:8]}...).  Stale token cleared — "
                "re-authenticate via /setup."
            )

        # Prefer whatever's on disk over any in-memory guess the caller
        # passed in: another process may have rotated while we were
        # waiting for the lock.
        refresh_token = tokens['refresh_token']
        try:
            result = refresh_access_token(client_id, refresh_token)
        except urllib.error.HTTPError as e:
            # Body can only be read once — parse here, carry it along
            # on the exception for the caller's logging.
            e.oauth_error = _read_oauth_error(e) if e.code == 400 else ''
            if e.oauth_error == 'invalid_grant':
                # Spotify: "Do not retry the refresh — discard the stored
                # token and redirect the user to sign in again."  Since
                # June 2026 this fires 6 months after the original auth.
                delete_tokens()
                log.error("Spotify refresh token expired/revoked "
                          "(invalid_grant) — stored token discarded. "
                          "Re-authenticate via the /setup page.")
                raise SpotifyReauthRequired(
                    "Spotify refresh token expired — re-authenticate "
                    "via the /setup page.") from e
            raise
        new_rt = result.get('refresh_token') or refresh_token
        # Spotify returns ``scope`` on every refresh — persist it so the
        # service can flag scope drift even after a clean restart.
        if result.get('scope') is None:
            log.warning("Refresh response missing 'scope' field — keys=%s",
                        sorted(result.keys()))
        save_tokens(client_id, new_rt, scope=result.get('scope'))
        if known_refresh_token and known_refresh_token != new_rt:
            log.info("Refresh token rotated by another process")
        return client_id, new_rt, result


def get_access_token(config_client_id=None):
    """Get a Spotify access token (sync). For standalone fetch.py runs.

    Uses the PKCE token store with cross-process refresh_lock so the
    service and fetch scripts can't race each other through PKCE token
    rotation.  The service passes ``--access-token`` instead; this
    exists for manual/cron use.
    """
    _, _, result = _refresh_under_file_lock(config_client_id=config_client_id)
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
        self._scope = None  # space-separated, mirrors what Spotify granted
        self._authorized_at = None  # epoch of original OAuth grant
        self._last_age_warn = 0.0   # monotonic; rate-limits aging warnings
        self.revoked = False
        self._refresh_lock = asyncio.Lock()

    def load(self, config_client_id=None):
        """Load credentials from token store. Returns True if valid credentials found.

        If config_client_id is provided and differs from the stored client_id,
        the stale token is cleared — the user switched Spotify apps.
        """
        tokens = load_tokens()
        if tokens and tokens.get('client_id') and tokens.get('refresh_token'):
            stored_id = tokens['client_id']
            if config_client_id and stored_id != config_client_id:
                log.warning("Spotify client_id changed (%s... → %s...) — "
                            "clearing stale token, re-auth required",
                            stored_id[:8], config_client_id[:8])
                delete_tokens()
                return False
            self._client_id = stored_id
            self._refresh_token = tokens['refresh_token']
            self._scope = tokens.get('scope')
            self._authorized_at = tokens.get('authorized_at')
            self._warn_if_grant_aging()
            log.info("Spotify credentials loaded (client_id: %s...)",
                     self._client_id[:8])
            if self._scope:
                log.info("Granted scopes: %s", self._scope)
            else:
                log.info("Granted scopes: unknown (token predates scope "
                         "tracking — will be captured on next refresh)")
            return True
        if tokens is not None:
            log.info("Token file exists but incomplete — waiting for setup")
        else:
            log.warning("No Spotify tokens found — use the /setup page to connect")
        return False

    def set_credentials(self, client_id, refresh_token, access_token=None,
                        expires_in=3600, scope=None, authorized_at=None):
        """Set credentials directly (used after OAuth callback)."""
        self._client_id = client_id
        self._refresh_token = refresh_token
        self._access_token = access_token
        self._token_expiry = time.monotonic() + expires_in - 300 if access_token else 0
        if scope is not None:
            self._scope = scope
        if authorized_at is not None:
            self._authorized_at = authorized_at
        self.revoked = False

    def clear(self):
        """Clear all credentials (used on logout)."""
        self._client_id = None
        self._refresh_token = None
        self._access_token = None
        self._scope = None
        self._token_expiry = 0

    @property
    def granted_scope(self):
        """Space-separated scope string Spotify granted, or None if unknown."""
        return self._scope

    @property
    def auth_age_days(self):
        """Days since the original OAuth grant, or None if unknown
        (token predates authorized_at tracking)."""
        if not self._authorized_at:
            return None
        return int((time.time() - self._authorized_at) / 86400)

    @property
    def reauth_recommended(self):
        """True when the grant is close enough to Spotify's 6-month
        refresh-token expiry that the user should re-auth soon."""
        if not self._authorized_at:
            return False
        return time.time() - self._authorized_at > REAUTH_WARN_AFTER

    def _warn_if_grant_aging(self, min_interval=86400):
        """Log (at most once per ``min_interval``) when the grant nears
        Spotify's 6-month refresh-token expiry."""
        if not self.reauth_recommended:
            return
        now = time.monotonic()
        if self._last_age_warn and now - self._last_age_warn < min_interval:
            return
        self._last_age_warn = now
        age = time.time() - self._authorized_at
        days_left = max(0, int((REFRESH_TOKEN_TTL - age) / 86400))
        log.warning("Spotify authorization is %d days old — Spotify "
                    "expires refresh tokens 6 months after the original "
                    "grant. Re-authenticate via /setup within ~%d days "
                    "to avoid interruption.", int(age / 86400), days_left)

    async def get_token(self):
        """Get a valid access token, refreshing if needed."""
        if self._access_token and time.monotonic() < self._token_expiry:
            return self._access_token
        return await self._refresh()

    async def _refresh(self):
        """Refresh the access token via PKCE.

        Two levels of serialisation:

          * ``self._refresh_lock`` is an asyncio lock that prevents
            concurrent refreshes *within* this process.
          * The sync helper ``_refresh_under_file_lock`` acquires a
            cross-process ``fcntl.flock`` on the token file so a
            concurrent fetch.py script can't race us.

        The sync helper is run in an executor so the ``flock`` syscall
        doesn't block the event loop if another process is holding it.
        """
        async with self._refresh_lock:
            # Re-check after acquiring the in-process lock — another
            # coroutine may have already refreshed while we waited.
            if self._access_token and time.monotonic() < self._token_expiry:
                return self._access_token

            if not self._client_id or not self._refresh_token:
                raise RuntimeError("No Spotify credentials")

            loop = asyncio.get_running_loop()
            try:
                client_id, new_rt, result = await loop.run_in_executor(
                    None,
                    _refresh_under_file_lock,
                    None,                # config_client_id (not validated here)
                    self._refresh_token, # for rotation-detection logging
                )
            except SpotifyReauthRequired:
                # Token already discarded on disk by the sync helper —
                # mirror that in memory so callers stop trying.
                self.revoked = True
                raise
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    log.warning("Token refresh failed (400): %s",
                                getattr(e, 'oauth_error', ''))
                raise

            rotated = new_rt != self._refresh_token
            self._refresh_token = new_rt
            self._client_id = client_id
            new_scope = result.get('scope')
            if new_scope and new_scope != self._scope:
                if self._scope is None:
                    log.info("Granted scopes (first refresh): %s", new_scope)
                else:
                    log.warning("Granted scopes changed: %r -> %r",
                                self._scope, new_scope)
                self._scope = new_scope
            if rotated:
                log.info("Refresh token rotated")

            self._access_token = result['access_token']
            self._token_expiry = time.monotonic() + result.get('expires_in', 3600) - 300

            if self.revoked:
                self.revoked = False
                log.info("Token revocation cleared — refresh succeeded")
            log.info("Access token refreshed (expires in %ds)", result.get('expires_in', 0))
            return self._access_token

    async def start_keepalive(self, interval=2700):
        """Proactively refresh token every `interval` seconds (default 45min)
        to prevent Spotify's PKCE refresh token from expiring."""
        self.stop_keepalive()
        self._keepalive_task = asyncio.create_task(self._keepalive_loop(interval))

    async def _keepalive_loop(self, interval):
        try:
            while True:
                await asyncio.sleep(interval)
                if self.revoked or not self.is_configured:
                    break
                try:
                    await self.get_token()
                    log.info("Keepalive: token refreshed")
                    self._warn_if_grant_aging()
                except Exception as e:
                    log.warning("Keepalive refresh failed: %s", e)
        except asyncio.CancelledError:
            return

    def stop_keepalive(self):
        task = getattr(self, '_keepalive_task', None)
        if task:
            task.cancel()
            self._keepalive_task = None

    async def close(self):
        pass  # no persistent session — parity with RemoteSpotifyAuth

    @property
    def is_configured(self):
        return bool(self._client_id and self._refresh_token)


class RemoteSpotifyAuth:
    """Fetches access tokens from a token master device.

    Drop-in replacement for SpotifyAuth on follower devices.
    The master runs a normal SpotifyAuth and exposes GET /token.
    """

    def __init__(self, master_url: str):
        self._master_url = master_url.rstrip('/')
        self._access_token = None
        self._token_expiry = 0
        self._session: aiohttp.ClientSession | None = None
        self.revoked = False

    def load(self, config_client_id=None):
        """Always returns True — auth is delegated to the master."""
        return True

    def set_credentials(self, *args, **kwargs):
        pass

    def clear(self):
        self._access_token = None
        self._token_expiry = 0

    async def get_token(self):
        """Fetch token from master, with in-memory caching."""
        if self._access_token and time.monotonic() < self._token_expiry:
            return self._access_token
        if not self._session:
            self._session = aiohttp.ClientSession()
        try:
            async with self._session.get(
                f"{self._master_url}/token",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    log.warning("Token master returned %d", resp.status)
                    if self._access_token:
                        return self._access_token
                    raise RuntimeError(f"Token master returned {resp.status}")
                data = await resp.json()
                self._access_token = data['access_token']
                # Cache with buffer — re-fetch 5 min before master's expiry
                self._token_expiry = time.monotonic() + data.get('expires_in', 3600) - 300
                self.revoked = False
                log.info("Token fetched from master (expires in %ds)", data.get('expires_in', 0))
                return self._access_token
        except Exception as e:
            log.warning("Token master unreachable: %s", e)
            if self._access_token:
                return self._access_token
            raise

    async def start_keepalive(self, interval=2700):
        pass  # master handles keepalive

    def stop_keepalive(self):
        pass

    @property
    def auth_age_days(self):
        return None  # master owns the grant

    @property
    def reauth_recommended(self):
        return False

    @property
    def is_configured(self):
        return True

    async def close(self):
        if self._session:
            await self._session.close()
