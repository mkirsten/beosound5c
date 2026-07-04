"""Token store wrapper for Spotify PKCE credentials.

Persists ``client_id`` + ``refresh_token``.  Atomic write, partial-merge,
and refresh-lock semantics live in ``lib.token_store``.
"""

import os

from lib.token_store import TokenStore

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_store = TokenStore("spotify_tokens.json", dev_dir=SCRIPT_DIR)


def load_tokens():
    """Return the saved token dict, or None."""
    return _store.load()


def save_tokens(client_id, refresh_token, scope=None, authorized_at=None):
    """Merge client_id + refresh_token (+ optional scope) into the store.

    Spotify returns ``scope`` in token-exchange and refresh responses;
    persisting it lets callers detect when a stored token was issued
    against a narrower scope set than the app currently asks for —
    a refresh won't re-grant scopes the user never approved, so missing
    scopes can only be fixed by a full re-auth.

    ``authorized_at`` (epoch seconds) is set only on the initial OAuth
    exchange, never on refresh — Spotify expires refresh tokens 6 months
    after the *original* user authorization (June 2026 policy), and
    token rotation does not extend that window.  ``save_merge`` keeps
    the stored value when the arg is None.
    """
    update = {"client_id": client_id, "refresh_token": refresh_token}
    if scope is not None:
        update["scope"] = scope
    if authorized_at is not None:
        update["authorized_at"] = authorized_at
    return _store.save_merge(update)


def delete_tokens():
    return _store.delete()


def refresh_lock():
    """``with refresh_lock():`` — serialises concurrent refreshes."""
    return _store.refresh_lock()
