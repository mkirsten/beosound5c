"""Shared atomic token store for OAuth credentials.

Every source service that authenticates against an external provider
(Spotify, Plex, Tidal, Apple Music) needs to persist tokens across
restarts.  Historically each source had its own ``*_tokens.py`` with
~90% identical ``_find_store_path`` / ``save_tokens`` / ``load_tokens``
plumbing.  Each one independently reinvented the atomic-write pattern,
and bugs have slipped in along the way (see commits 566acb3 — don't
clobber refresh token on failed refresh; 28e02fb — OAuth session loss;
c45a1cf — shared spotify token master to prevent PKCE revocation races).

This module centralises the pattern:

  * Atomic writes via ``tempfile.mkstemp`` + ``os.replace``, with
    fall-through to a direct write if the parent directory is read-only.
  * Storage-path discovery: prefer ``/etc/beosound5c/<name>`` in
    production, fall back to a caller-provided dev directory.
  * ``refresh_lock()`` context manager — an ``fcntl.flock`` around the
    token file so two processes (service + fetch script) can't both
    refresh at the same time and revoke each other's tokens.
  * ``save_merge()`` — update specific fields without clobbering the
    rest.  This is what commit 566acb3 needed: a failed refresh must
    not wipe the existing refresh_token.

Per-source modules become thin wrappers that bind a filename + field
shape.  See ``sources/spotify/spotify_tokens.py`` for an example.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

log = logging.getLogger("token-store")

_PROD_DIR = "/etc/beosound5c"


class TokenStore:
    """Atomic JSON-backed token store for a single source service.

    Parameters
    ----------
    filename:
        Base filename (e.g. ``"spotify_tokens.json"``).
    dev_dir:
        Fallback directory used in dev/test when ``/etc/beosound5c``
        isn't writable.  Usually the caller's ``SCRIPT_DIR``.
    prod_dir:
        Override the production directory (tests use this).
    """

    def __init__(self, filename: str, *, dev_dir: str, prod_dir: str = _PROD_DIR):
        self._filename = filename
        self._paths = [
            os.path.join(prod_dir, filename),
            os.path.join(dev_dir, filename),
        ]

    # ── Path discovery ──

    def path(self) -> str:
        """Best existing or writable path for the token file."""
        for p in self._paths:
            if os.path.exists(p):
                return p
        for p in self._paths:
            d = os.path.dirname(p)
            if os.path.isdir(d) and os.access(d, os.W_OK):
                return p
        return self._paths[-1]

    # ── Load ──

    def load(self) -> dict | None:
        """Return the current token dict, or None if missing/corrupt."""
        path = self.path()
        try:
            with open(path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    # ── Save ──

    def save(self, data: dict) -> str:
        """Atomically write ``data`` as the full token payload.

        ``updated_at`` is injected automatically.  Callers should *not*
        use this to update a single field while leaving others intact —
        use :meth:`save_merge` for that.
        """
        payload = dict(data)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self._write(payload)

    def save_merge(self, updates: dict) -> str:
        """Merge ``updates`` on top of the existing token file.

        This is the correct path for refresh flows: a partial failure
        must not clobber fields it didn't touch.  See commit 566acb3 —
        a failed Spotify refresh used to wipe the refresh_token because
        the caller re-saved the *new* (empty) dict over the old one.
        """
        existing = self.load() or {}
        existing.update(updates)
        return self.save(existing)

    def _write(self, payload: dict) -> str:
        path = self.path()
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        content = json.dumps(payload, indent=2) + "\n"

        # Preferred: atomic temp+rename in the target directory.
        try:
            fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(content)
                    # fsync before the rename: the SD cards run with
                    # commit=120 (sd-hardening), so without this a power
                    # cut within ~2 min can leave an empty file — and a
                    # lost refresh_token means manual re-auth on a
                    # headless device.
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
                try:
                    dfd = os.open(d, os.O_DIRECTORY)
                    try:
                        os.fsync(dfd)
                    finally:
                        os.close(dfd)
                except OSError:
                    pass  # directory fsync is best-effort (not on all platforms)
                log.info("Tokens saved to %s", path)
                return path
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            # Parent directory not writable (read-only system partition
            # or similar) — fall through to direct write of the file
            # itself, which may still be writable.
            if e.errno not in (errno.EACCES, errno.EROFS, errno.EPERM):
                raise
            log.debug("Atomic write to %s failed (%s); trying direct write", d, e)

        with open(path, "w") as f:
            f.write(content)
        log.info("Tokens saved to %s (direct write)", path)
        return path

    # ── Delete ──

    def delete(self) -> str | None:
        """Remove the token file.  Returns the path deleted, or None."""
        path = self.path()
        if os.path.exists(path):
            os.unlink(path)
            return path
        return None

    # ── Refresh lock ──

    @contextmanager
    def refresh_lock(self) -> Iterator[None]:
        """Serialise token refreshes across processes.

        Holds an exclusive ``fcntl.flock`` on ``<token_file>.lock`` for
        the duration of the ``with`` block.  A second refresh attempt
        (from a concurrent fetch script or a restarted service) blocks
        until the first releases the lock, at which point it should
        reload tokens and check whether a refresh is still needed.

        Locking is best-effort: if ``fcntl`` is unavailable (Windows,
        exotic FS) the context manager is a no-op.  This keeps the
        plumbing usable in unit tests on any platform.
        """
        lock_path = self.path() + ".lock"
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        try:
            import fcntl  # type: ignore[import-not-found]
        except ImportError:
            yield
            return
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as e:
            log.warning("Cannot open token lock %s (%s) — skipping", lock_path, e)
            yield
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
