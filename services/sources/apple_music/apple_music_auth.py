"""
Apple Music authentication — developer token + user token management.

Developer token: pre-built JWT from Music Assistant (Apache 2.0 licensed).
  - No signing or Apple Developer account required.
  - When the bundled token is expired or near expiry, a fresh one is
    auto-fetched from the latest Music Assistant release and cached on
    disk (ensure_fresh_developer_token), so an old build keeps working.
  - See DEVELOPER_TOKEN below for manual extraction instructions.

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
from apple_music_tokens import load_tokens, load_dev_token, save_dev_token

log = logging.getLogger('beo-source-apple-music')

# ── Developer Token ──
# Extracted from Music Assistant (Apache 2.0) — music_assistant/helpers/app_vars.py, app_var(8)
# (verified from release 2.9.5; live-checked against api.music.apple.com)
# Last updated: 2026-07-07
# Expires: 2026-10-22
# To refresh: check https://github.com/music-assistant/server for latest release,
#   find music_assistant/helpers/app_vars.py, decode app_var(8) and paste here.
#   Note: MA's dev branch is moving credentials into a build-time app_secrets.json
#   (key "apple_music_token") bundled in the PyPI wheel/Docker image — if
#   app_vars.py no longer contains the blob, extract from the wheel instead.
DEVELOPER_TOKEN = (
    "eyJhbGciOiJFUzI1NiIsImtpZCI6IkFSRzJSN0xEOTkiLCJ0eXAiOiJKV1QifQ."
    "eyJpc3MiOiJHVVlGQUs4REM2IiwiZXhwIjoxNzkyNzA0NTEzLCJpYXQiOjE3NzY5Mjc1MTN9."
    "MMnlSyXIrg5zWrogOunqcYgTzGBMr7otVBtGqU-ATbkvydnHydyWbhw-IJZV4pvE41OdyNrdLuC8Vd9oSPbC6Q"
)


def _resolve_developer_token():
    """Return the developer token: env override > bundled > auto-fetched.

    ``APPLE_MUSIC_DEV_TOKEN`` (secrets.env) always wins so a device can be
    pinned to a known token. The bundled token is preferred while valid;
    once it lapses, the last auto-fetched web-player token takes over
    (see ensure_fresh_developer_token).
    """
    override = os.environ.get('APPLE_MUSIC_DEV_TOKEN', '').strip()
    if override:
        return override
    if _token_valid(DEVELOPER_TOKEN):
        return DEVELOPER_TOKEN
    fetched = load_dev_token()
    if fetched and _token_valid(fetched):
        return fetched
    return DEVELOPER_TOKEN


def _token_valid(token, margin=0):
    """True if the JWT decodes and its exp is at least *margin*s away."""
    exp = developer_token_expiry(token)
    return bool(exp and exp > time.time() + margin)


MA_RELEASE_API = 'https://api.github.com/repos/music-assistant/server/releases/latest'


class _HttpRangeFile:
    """Minimal seekable file-like backed by HTTP range requests.

    Lets zipfile read a remote wheel's central directory + one member
    without downloading the whole 50+ MB archive — a Pi pulls only a few
    KB to extract app_vars.py.
    """

    def __init__(self, url, timeout=20):
        import urllib.request
        self._url = url
        self._timeout = timeout
        self._pos = 0
        data, hdrs = self._fetch(0, 0)
        cr = hdrs.get('Content-Range', '')
        self.size = (int(cr.split('/')[-1]) if '/' in cr
                     else int(hdrs.get('Content-Length', 0)))

    def _fetch(self, start, end):
        import urllib.request
        req = urllib.request.Request(
            self._url,
            headers={'User-Agent': 'Mozilla/5.0',
                     'Range': f'bytes={start}-{end}'})
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            return r.read(), r.headers

    def seek(self, off, whence=0):
        self._pos = (off if whence == 0
                     else self._pos + off if whence == 1
                     else self.size + off)
        return self._pos

    def tell(self):
        return self._pos

    def seekable(self):
        return True

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self._pos
        if n == 0 or self._pos >= self.size:
            return b''
        data, _ = self._fetch(self._pos, min(self._pos + n, self.size) - 1)
        self._pos += len(data)
        return data


def _decode_ma_app_vars(src):
    """Pull a valid Apple Music JWT out of Music Assistant's app_vars.py.

    MA obfuscates its credentials as base64(reversed(text)) joined on the
    'acb2' separator. Rather than hardcode the index (it drifts between
    releases), decode every part and return the first that looks like a
    non-expired Apple developer token.
    """
    import re
    m = re.search(r"\('([^']{400,})'\)", src)
    if not m:
        return None
    for part in m.group(1).split('acb2'):
        try:
            cand = base64.b64decode(part[::-1]).decode()
        except Exception:
            continue
        if cand.startswith('eyJh') and _token_valid(cand, margin=86400):
            return cand
    return None


def fetch_ma_developer_token(timeout=20):
    """Fetch a fresh Apple Music developer token from the latest Music
    Assistant release wheel (the same source the bundled token came from).

    Uses HTTP range requests so only a few KB are transferred. Blocking
    network I/O — call in an executor. Returns a validated token, or None.
    """
    import json
    import urllib.request
    import zipfile

    try:
        req = urllib.request.Request(
            MA_RELEASE_API, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rel = json.loads(r.read())
        wheel_url = next(
            (a['browser_download_url'] for a in rel.get('assets', [])
             if a['name'].endswith('-py3-none-any.whl')), None)
        if not wheel_url:
            log.warning("No wheel asset in the latest Music Assistant release")
            return None

        rf = _HttpRangeFile(wheel_url, timeout=timeout)
        with zipfile.ZipFile(rf) as z:
            src = z.read('music_assistant/helpers/app_vars.py').decode()
        token = _decode_ma_app_vars(src)
        if token:
            exp = developer_token_expiry(token)
            log.info("Fetched Apple Music developer token from %s (expires %s)",
                     rel.get('tag_name', '?'),
                     time.strftime('%Y-%m-%d', time.localtime(exp)))
            return token
        log.warning("No usable developer token in Music Assistant app_vars.py")
    except Exception as e:
        log.warning("Could not fetch developer token from Music Assistant: %s", e)
    return None


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
                "will NOT fix this. A fresh token will be auto-fetched from "
                "Music Assistant; to pin one manually, set "
                "APPLE_MUSIC_DEV_TOKEN in /etc/beosound5c/secrets.env.", exp)
        elif exp and exp - time.time() < 30 * 86400:
            log.warning("Apple Music developer token expires in %d days — "
                        "a replacement will be auto-fetched before it lapses.",
                        int((exp - time.time()) / 86400))

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

    def ensure_fresh_developer_token(self):
        """Fetch a replacement developer token when the resolved one is
        expired or within 30 days of expiry. Blocking network I/O — call
        in an executor. Returns True if a valid token is available.

        No-op when APPLE_MUSIC_DEV_TOKEN is set: an explicit override means
        the user pinned a token on purpose, so we don't second-guess it.
        """
        if os.environ.get('APPLE_MUSIC_DEV_TOKEN', '').strip():
            self.developer_token_valid = _token_valid(_resolve_developer_token())
            return self.developer_token_valid
        if _token_valid(_resolve_developer_token(), margin=30 * 86400):
            self.developer_token_valid = True
            return True
        token = fetch_ma_developer_token()
        if token:
            try:
                save_dev_token(token)
            except Exception as e:
                log.warning("Could not persist fetched developer token: %s", e)
            self.developer_token_valid = True
            return True
        self.developer_token_valid = _token_valid(_resolve_developer_token())
        return self.developer_token_valid

    def get_user_token(self):
        """Return the stored user token."""
        return self._user_token

    @property
    def storefront(self):
        return self._storefront or 'us'

    @property
    def is_configured(self):
        return bool(self._user_token and self.developer_token_valid)
