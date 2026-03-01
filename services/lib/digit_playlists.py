"""Shared digit playlist utilities for BeoSound 5c sources.

Used by:
  - fetch.py scripts (detect_digit_playlist, build_digit_mapping)
  - service.py sources (DigitPlaylistMixin for cached lookups)
"""

import json
import logging
import re

log = logging.getLogger(__name__)


def detect_digit_playlist(name):
    """Check if playlist name starts with a digit pattern like '5:' or '5 -'.
    Returns the digit (0-9) or None."""
    match = re.match(r'^(\d)[\s]*[:\-]', name)
    if match:
        return match.group(1)
    return None


def build_digit_mapping(playlists):
    """Build digit 0-9 mapping. Explicitly named playlists (e.g. '5: Jazz')
    get pinned to their digit; remaining slots filled alphabetically."""
    pinned = {}
    pinned_ids = set()
    for pl in playlists:
        digit = detect_digit_playlist(pl['name'])
        if digit is not None and digit not in pinned:
            pinned[digit] = pl
            pinned_ids.add(pl['id'])

    remaining = iter(pl for pl in playlists if pl['id'] not in pinned_ids)

    mapping = {}
    for slot in "0123456789":
        if slot in pinned:
            pl = pinned[slot]
        else:
            pl = next(remaining, None)
            if not pl:
                continue
        entry = {
            'id': pl['id'],
            'name': pl['name'],
            'image': pl.get('image'),
        }
        if pl.get('url'):
            entry['url'] = pl['url']
        mapping[slot] = entry

    return mapping


class DigitPlaylistMixin:
    """Mixin for source services that use digit playlists.

    Caches the digit playlists file in memory instead of re-reading
    from disk on every button press. Call `_reload_digit_playlists()`
    after a fetch/refresh to update the cache.

    Subclass must set `DIGIT_PLAYLISTS_FILE` as a class or instance attribute.
    """

    _digit_cache = None  # {digit_str: {id, name, image, ...}}

    def _reload_digit_playlists(self):
        """Reload digit playlists from disk into cache."""
        try:
            with open(self.DIGIT_PLAYLISTS_FILE) as f:
                self._digit_cache = json.load(f)
        except FileNotFoundError:
            self._digit_cache = {}
        except Exception as e:
            log.warning("Failed to load digit playlists: %s", e)
            self._digit_cache = {}

    def _get_digit_playlist(self, digit):
        """Look up a digit playlist from the cached mapping."""
        if self._digit_cache is None:
            self._reload_digit_playlists()
        info = self._digit_cache.get(str(digit))
        if info and info.get('id'):
            return info
        return None

    def _get_digit_names(self):
        """Return {digit: name} dict for status responses."""
        if self._digit_cache is None:
            self._reload_digit_playlists()
        return {
            d: info['name']
            for d, info in (self._digit_cache or {}).items()
            if info and info.get('name')
        }
