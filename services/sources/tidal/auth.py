"""
TIDAL authentication — OAuth device login via tidalapi.

Uses tidalapi.Session for OAuth device flow:
  - start_device_login() begins the flow, returns a URL for the user
  - complete_login() blocks until the user completes auth (run in executor)
  - Tokens are saved/loaded via tokens.py
"""

import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tokens import load_tokens, save_tokens

log = logging.getLogger('beo-source-tidal')


class TidalAuth:
    """Manages TIDAL OAuth session for the long-running service."""

    def __init__(self):
        self._session = None
        self._user_name = None
        self.revoked = False

    def _create_session(self):
        """Create a fresh tidalapi Session."""
        import tidalapi
        session = tidalapi.Session()
        return session

    def load(self):
        """Load tokens from disk and restore session. Returns True if valid."""
        tokens = load_tokens()
        if not tokens or not tokens.get('access_token'):
            if tokens is not None:
                log.info("Token file exists but incomplete — waiting for setup")
            else:
                log.info("No TIDAL tokens found — use the setup page to connect")
            return False

        import tidalapi
        session = self._create_session()
        try:
            # Convert stored float timestamp back to datetime for tidalapi
            expiry_raw = tokens.get('expiry_time')
            expiry_dt = (datetime.fromtimestamp(expiry_raw, tz=timezone.utc)
                         if expiry_raw else None)
            session.load_oauth_session(
                token_type=tokens['token_type'],
                access_token=tokens['access_token'],
                refresh_token=tokens.get('refresh_token'),
                expiry_time=expiry_dt,
            )
            if session.check_login():
                self._session = session
                try:
                    self._user_name = session.user.name
                except Exception:
                    self._user_name = None
                log.info("TIDAL session restored (user: %s)", self._user_name or "unknown")
                return True
            else:
                log.warning("TIDAL session expired — re-authentication required")
                self.revoked = True
                return False
        except Exception as e:
            log.warning("Could not restore TIDAL session: %s", e)
            return False

    def start_device_login(self):
        """Start OAuth device login flow. Returns (login_url, future) tuple.
        The future must be awaited in an executor (it blocks)."""
        session = self._create_session()
        login, future = session.login_oauth()
        # login has: verification_uri_complete, user_code, expires_in
        return session, login, future

    def complete_login(self, session, future):
        """Block until the user completes the device login. Call in executor.
        Returns True on success."""
        future.result()  # blocks until login complete or timeout
        if session.check_login():
            self._session = session
            self.revoked = False
            try:
                self._user_name = session.user.name
            except Exception:
                self._user_name = None
            self._save_session()
            log.info("TIDAL login complete (user: %s)", self._user_name or "unknown")
            return True
        return False

    def _save_session(self):
        """Persist current session tokens to disk."""
        if not self._session:
            return
        try:
            save_tokens(
                token_type=self._session.token_type,
                access_token=self._session.access_token,
                refresh_token=self._session.refresh_token,
                expiry_time=self._session.expiry_time.timestamp()
                    if self._session.expiry_time else None,
            )
            log.info("TIDAL tokens saved to disk")
        except Exception as e:
            log.warning("Could not save TIDAL tokens: %s", e)

    def refresh_if_needed(self):
        """Refresh the token if it's about to expire. Returns True if session is valid."""
        if not self._session:
            return False
        try:
            if self._session.check_login():
                return True
            # Try token refresh
            self._session.token_refresh(self._session.refresh_token)
            if self._session.check_login():
                self._save_session()
                return True
        except Exception as e:
            log.warning("TIDAL token refresh failed: %s", e)
        self.revoked = True
        return False

    def clear(self):
        """Clear all credentials."""
        self._session = None
        self._user_name = None

    @property
    def session(self):
        return self._session

    @property
    def is_configured(self):
        return self._session is not None and not self.revoked

    @property
    def user_name(self):
        return self._user_name
