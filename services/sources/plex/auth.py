"""
Plex authentication — PIN-based OAuth via plexapi.

Uses MyPlexPinLogin for OAuth flow:
  - start_oauth() begins the flow, returns (pinlogin, oauth_url)
  - check_login() polls for completion
  - Tokens are saved/loaded via tokens.py
  - Plex tokens don't expire — no refresh needed
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tokens import load_tokens, save_tokens

log = logging.getLogger('beo-source-plex')


class PlexAuth:
    """Manages Plex authentication for the long-running service."""

    def __init__(self):
        self._server = None
        self._token = None
        self._user_name = None
        self._server_url = None
        self._server_name = None

    def load(self):
        """Load tokens from disk and connect to server. Returns True if valid."""
        tokens = load_tokens()
        if not tokens or not tokens.get('auth_token'):
            if tokens is not None:
                log.info("Token file exists but incomplete - waiting for setup")
            else:
                log.info("No Plex tokens found - use the setup page to connect")
            return False

        try:
            from plexapi.server import PlexServer
            server_url = tokens['server_url']
            auth_token = tokens['auth_token']
            server = PlexServer(server_url, auth_token, timeout=10)
            self._server = server
            self._token = auth_token
            self._server_url = server_url
            self._server_name = tokens.get('server_name', server.friendlyName)
            self._user_name = tokens.get('user_name')
            log.info("Plex session restored (user: %s, server: %s)",
                     self._user_name or "unknown", self._server_name)
            return True
        except Exception as e:
            log.warning("Could not restore Plex session: %s", e)
            return False

    def start_oauth(self):
        """Start PIN-based OAuth flow. Returns (pinlogin, oauth_url).
        pinlogin.run() spawns a background thread for polling."""
        from plexapi.myplex import MyPlexPinLogin
        pinlogin = MyPlexPinLogin(oauth=True)
        pinlogin.run(timeout=300)
        oauth_url = pinlogin.oauthUrl()
        return pinlogin, oauth_url

    def check_login(self, pinlogin):
        """Check if the OAuth login has completed.
        Returns True on success, False if still pending, raises on error."""
        if not pinlogin.checkLogin():
            return False

        token = pinlogin.token
        if not token:
            return False

        # Got a token — discover servers
        from plexapi.myplex import MyPlexAccount
        account = MyPlexAccount(token=token)
        self._user_name = account.username or account.email

        # Find a server with a music library
        server = None
        server_url = None
        for resource in account.resources():
            if 'server' not in resource.provides:
                continue
            try:
                srv = resource.connect(timeout=10)
                # Check for music library
                for section in srv.library.sections():
                    if section.type == 'artist':
                        server = srv
                        server_url = srv._baseurl
                        break
                if server:
                    break
            except Exception as e:
                log.debug("Could not connect to Plex server %s: %s", resource.name, e)
                continue

        if not server:
            log.error("No Plex server with a music library found")
            return False

        self._server = server
        self._token = token
        self._server_url = server_url
        self._server_name = server.friendlyName

        save_tokens(
            auth_token=token,
            server_url=server_url,
            server_name=self._server_name,
            user_name=self._user_name,
        )
        log.info("Plex login complete (user: %s, server: %s)",
                 self._user_name, self._server_name)
        return True

    def clear(self):
        """Clear all credentials."""
        self._server = None
        self._token = None
        self._user_name = None
        self._server_url = None
        self._server_name = None

    @property
    def server(self):
        return self._server

    @property
    def token(self):
        return self._token

    @property
    def is_configured(self):
        return self._server is not None

    @property
    def user_name(self):
        return self._user_name

    @property
    def server_url(self):
        return self._server_url

    @property
    def server_name(self):
        return self._server_name
