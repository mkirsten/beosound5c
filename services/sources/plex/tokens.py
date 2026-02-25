"""
Atomic token storage for Plex user credentials.

Stores auth_token and server info in a JSON file.
Writes are atomic (temp file + rename) so a crash mid-write never corrupts.
Plex tokens don't expire — no refresh needed.

Storage locations (first writable wins):
  1. /etc/beosound5c/plex_tokens.json  (production on Pi)
  2. <script_dir>/plex_tokens.json      (dev fallback)
"""

import json
import os
import tempfile
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

STORE_PATHS = [
    "/etc/beosound5c/plex_tokens.json",
    os.path.join(SCRIPT_DIR, "plex_tokens.json"),
]


def _find_store_path():
    """Find the best token store path (first existing, or first writable)."""
    for path in STORE_PATHS:
        if os.path.exists(path):
            return path
    for path in STORE_PATHS:
        d = os.path.dirname(path)
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return path
    return STORE_PATHS[-1]


def load_tokens():
    """Load tokens from disk. Returns dict or None if not found."""
    path = _find_store_path()
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_tokens(auth_token, server_url, server_name, user_name):
    """Atomically save tokens to disk."""
    path = _find_store_path()
    data = {
        "auth_token": auth_token,
        "server_url": server_url,
        "server_name": server_name,
        "user_name": user_name,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    return path


def delete_tokens():
    """Delete the token file from disk. Returns the path deleted, or None."""
    path = _find_store_path()
    if os.path.exists(path):
        os.unlink(path)
        return path
    return None
