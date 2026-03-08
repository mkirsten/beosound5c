"""
Atomic token storage for Spotify PKCE credentials.

Stores client_id + refresh_token in a JSON file.  Writes are atomic
(temp file + rename) so a crash mid-write never corrupts the file.

Storage locations (first writable wins):
  1. /etc/beosound5c/spotify_tokens.json  (production on Pi)
  2. <script_dir>/spotify_tokens.json      (dev fallback)
"""

import json
import os
import tempfile
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

STORE_PATHS = [
    "/etc/beosound5c/spotify_tokens.json",
    os.path.join(SCRIPT_DIR, "spotify_tokens.json"),
]


def _find_store_path():
    """Find the best token store path (first existing, or first writable)."""
    # Prefer an existing file
    for path in STORE_PATHS:
        if os.path.exists(path):
            return path
    # Fall back to first writable directory
    for path in STORE_PATHS:
        d = os.path.dirname(path)
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return path
    # Last resort: dev path
    return STORE_PATHS[-1]


def load_tokens():
    """Load tokens from disk. Returns dict or None if not found."""
    path = _find_store_path()
    try:
        with open(path) as f:
            data = json.load(f)
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_tokens(client_id, refresh_token):
    """Save tokens to disk. Tries atomic (temp+rename), falls back to direct write."""
    path = _find_store_path()
    data = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    content = json.dumps(data, indent=2) + "\n"

    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)

    # Try atomic write first (needs write permission on parent directory)
    try:
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp, path)
            return path
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except OSError:
        pass  # Can't create temp file in dir — fall through to direct write

    # Direct write — file itself is writable even if directory isn't
    with open(path, "w") as f:
        f.write(content)

    return path


def delete_tokens():
    """Delete the token file from disk. Returns the path deleted, or None."""
    path = _find_store_path()
    if os.path.exists(path):
        os.unlink(path)
        return path
    return None
