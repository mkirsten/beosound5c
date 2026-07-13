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
import urllib3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plex_tokens import load_tokens, save_tokens

# Shared library (services/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib.config import cfg

# Suppress InsecureRequestWarning for self-signed Plex certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
            import requests
            server_url = tokens['server_url']
            auth_token = tokens['auth_token']
            sess = requests.Session()
            sess.verify = False
            server = PlexServer(server_url, auth_token, timeout=10, session=sess)
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
        NOTE: Do NOT call pinlogin.run() — it spawns a background polling
        thread that races with our manual check_login() calls, causing the
        PIN to be marked expired prematurely. We poll manually instead."""
        from plexapi.myplex import MyPlexPinLogin
        pinlogin = MyPlexPinLogin(oauth=True)
        oauth_url = pinlogin.oauthUrl()
        return pinlogin, oauth_url

    def check_login(self, pinlogin):
        """Check if the OAuth login has completed.
        Returns True on success, False if still pending, raises on error.
        Calls _checkLogin() directly to avoid plexapi's checkLogin() which
        treats 429 rate limits as permanent expiry."""
        if pinlogin.expired:
            raise TimeoutError("Plex PIN login expired")
        try:
            result = pinlogin._checkLogin()
            if not result:
                # Log PIN state periodically for debugging
                if hasattr(pinlogin, '_debug_count'):
                    pinlogin._debug_count += 1
                else:
                    pinlogin._debug_count = 1
                if pinlogin._debug_count % 20 == 1:  # every ~60s at 3s poll
                    log.info("PIN check #%d: id=%s, expired=%s, token=%s",
                             pinlogin._debug_count, pinlogin._id,
                             pinlogin.expired, bool(pinlogin.token))
                return False
        except Exception as e:
            if '429' in str(e) or 'rate' in str(e).lower():
                log.debug("Plex PIN check rate-limited, will retry")
                return False
            if '404' in str(e) or 'not_found' in str(e):
                # Normal PIN lifecycle — plex.tv expires unused PINs after
                # ~15 min; a fresh one is created on the next view open.
                log.info("Plex PIN %s expired — a new PIN will be issued", pinlogin._id)
            else:
                log.warning("PIN check exception: %s", e)
            pinlogin.expired = True
            raise

        token = pinlogin.token
        if not token:
            return False

        # Got a token — discover servers
        from plexapi.myplex import MyPlexAccount
        from plexapi.server import PlexServer
        import requests
        account = MyPlexAccount(token=token)
        self._user_name = account.username or account.email

        # Find a server with a music library
        server = None
        server_url = None

        # Try configured URL first (with HTTPS fallback)
        configured_url = cfg("plex", "url")
        if configured_url:
            urls_to_try = [configured_url]
            if configured_url.startswith("http://"):
                urls_to_try.append(configured_url.replace("http://", "https://", 1))
            elif configured_url.startswith("https://"):
                urls_to_try.append(configured_url.replace("https://", "http://", 1))
            log.info("Configured URL: %s (will try: %s)", configured_url,
                     ", ".join(urls_to_try))
            for url in urls_to_try:
                try:
                    # Create a session that accepts self-signed certs
                    sess = requests.Session()
                    sess.verify = False
                    log.info("Trying %s ...", url)
                    srv = PlexServer(url, token, timeout=10, session=sess)
                    sections = srv.library.sections()
                    log.info("  %s: connected, %d sections: %s", url,
                             len(sections),
                             [(s.title, s.type) for s in sections])
                    for section in sections:
                        if section.type == 'artist':
                            server = srv
                            server_url = url
                            log.info("Connected via configured URL: %s", url)
                            break
                    if server:
                        break
                except Exception as e:
                    log.info("Configured URL %s failed: %s", url, e)

        # Fall back to plexapi resource discovery
        if not server:
            log.info("Trying plexapi resource discovery...")
            for resource in account.resources():
                if 'server' not in resource.provides:
                    continue
                conn_urls = [c.uri for c in resource.connections]
                log.info("Trying server: %s (connections: %s)",
                         resource.name, conn_urls)
                try:
                    srv = resource.connect(timeout=10)
                    for section in srv.library.sections():
                        if section.type == 'artist':
                            server = srv
                            server_url = srv._baseurl
                            log.info("Connected via discovery: %s at %s",
                                     resource.name, server_url)
                            break
                    if server:
                        break
                except Exception as e:
                    log.warning("Server %s unreachable: %s", resource.name, e)
                    continue

        if not server:
            raise ValueError("Your Plex server has no Music library. Add one in Plex Settings → Libraries to use Plex on BeoSound 5c.")

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
