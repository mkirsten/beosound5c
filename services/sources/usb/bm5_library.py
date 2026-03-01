"""BM5Library -- SQLite-powered BeoMaster 5 library browser."""

import logging
import sqlite3
import time
import urllib.parse
from pathlib import Path

from lib.file_playback import ARTWORK_NAMES, ARTWORK_EXTS
from file_browser import _find_artwork, _list_real_dir

log = logging.getLogger('beo-usb')


class BM5Library:
    """Database-powered browser for BeoMaster 5 drives.

    Opens nmusic.db read-only and provides structured browsing by
    Artist, Album, Genre, and Folders.
    """

    def __init__(self, mount_path, name="BM5"):
        self.mount = Path(mount_path)
        self.name = name
        # Partition root contains BM-Share/ and Cache/ at top level
        self.music_root = self.mount / "BM-Share" / "Music"
        self.db_path = self.mount / "Cache" / "Data" / "nmusic.db"
        self._conn = None
        self._album_art = {}    # {album_id: path_str or None} — preloaded
        self._artist_art = {}   # {artist_id: path_str or None} — preloaded

    @property
    def available(self):
        return self.db_path.exists() and self.music_root.is_dir()

    def open(self):
        if self._conn:
            return
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {self.db_path}")

        t0 = time.monotonic()

        # Copy entire DB into RAM so queries never touch the spinning disk
        file_conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        mem_conn = sqlite3.connect(":memory:")
        file_conn.backup(mem_conn)
        file_conn.close()

        self._conn = mem_conn
        self._conn.row_factory = sqlite3.Row
        _collation = lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower())
        self._conn.create_collation("i_en_UK", _collation)
        self._conn.create_collation("i_sv_SE", _collation)

        # Pre-scan artwork availability (disk I/O now, instant lookups later)
        self._preload_artwork()

        db_mb = self.db_path.stat().st_size / (1024 * 1024)
        log.info("BM5 library preloaded in %.1fs (%.1f MB, %d albums, %d artists): %s",
                 time.monotonic() - t0, db_mb,
                 len(self._album_art), len(self._artist_art), self.db_path)

    def _preload_artwork(self):
        """Pre-scan all album artwork paths so browse never hits the disk."""
        c = self._conn.cursor()
        rows = c.execute(
            "SELECT id, large_cover_url, container_id, album_artist_id FROM album"
        ).fetchall()
        for r in rows:
            path = self._album_artwork_path(r)
            self._album_art[r['id']] = path
            # First album cover per artist
            aid = r['album_artist_id']
            if aid and aid not in self._artist_art:
                self._artist_art[aid] = path

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _translate_path(self, win_path):
        """Translate Windows path to Linux path.

        Handles both music paths (E:\\BM-Share\\Music\\...) and
        cover art paths (E:\\Cache\\Covers\\...) by stripping the
        drive letter and prepending the mount point.
        """
        if not win_path:
            return None
        # Normalize separators
        p = win_path.replace('\\', '/')
        # Strip drive letter (e.g. "E:/")
        if len(p) > 2 and p[1] == ':':
            p = p[2:]
        # Strip leading slash
        p = p.lstrip('/')
        return str(self.mount / p)

    def _album_artwork_path(self, album_row):
        """Get the 512px cover art path for an album."""
        keys = album_row.keys()
        cover = album_row['large_cover_url'] if 'large_cover_url' in keys else None
        if cover:
            translated = self._translate_path(cover)
            if translated and Path(translated).is_file():
                return translated
        # Fallback: folder.jpg in album directory (container_id is the folder path)
        container = album_row['container_id'] if 'container_id' in keys else None
        if container:
            album_dir = Path(self._translate_path(container))
            if album_dir.is_dir():
                for name in ARTWORK_NAMES:
                    for ext in ARTWORK_EXTS:
                        candidate = album_dir / f"{name}{ext}"
                        if candidate.is_file():
                            return str(candidate)
        return None

    # -- Browse methods --

    def browse(self, path=""):
        """Route a virtual path to the correct browse method."""
        if not path:
            return self._browse_root()
        parts = path.split('/', 1)
        category = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        handlers = {
            'artists': self._browse_artists,
            'albums': self._browse_albums,
            'genres': self._browse_genres,
            'folders': self._browse_folders,
        }
        handler = handlers.get(category)
        if handler:
            return handler(rest)
        return None

    def _browse_root(self):
        """Top-level categories with counts."""
        c = self._conn.cursor()
        artist_count = c.execute("SELECT COUNT(*) FROM album_artist").fetchone()[0]
        album_count = c.execute("SELECT COUNT(*) FROM album").fetchone()[0]
        genre_count = c.execute("SELECT COUNT(DISTINCT genre) FROM track WHERE genre IS NOT NULL AND genre != ''").fetchone()[0]
        return {
            "path": "",
            "parent": None,
            "name": self.name,
            "items": [
                {"type": "category", "name": "Artists", "id": "artists", "path": "artists", "icon": "microphone-stage", "count": artist_count},
                {"type": "category", "name": "Albums", "id": "albums", "path": "albums", "icon": "vinyl-record", "count": album_count},
                {"type": "category", "name": "Genres", "id": "genres", "path": "genres", "icon": "music-notes-simple", "count": genre_count},
                {"type": "category", "name": "Folders", "id": "folders", "path": "folders", "icon": "folder", "count": 0},
            ]
        }

    def _browse_artists(self, rest):
        if not rest:
            return self._list_artists()
        # rest could be an artist ID
        return self._list_artist_albums(rest)

    def _list_artists(self):
        c = self._conn.cursor()
        rows = c.execute("""
            SELECT aa.id, aa.name,
                   COUNT(a.id) as album_count,
                   MIN(a.large_cover_url) as first_cover
            FROM album_artist aa
            LEFT JOIN album a ON a.album_artist_id = aa.id
            GROUP BY aa.id
            ORDER BY aa.normalized_name
        """).fetchall()
        items = []
        for r in rows:
            items.append({
                "type": "artist",
                "name": r['name'],
                "id": str(r['id']),
                "path": f"artists/{r['id']}",
                "album_count": r['album_count'],
                "artwork": self._artist_art.get(r['id']) is not None,
            })
        return {
            "path": "artists",
            "parent": "",
            "name": "Artists",
            "items": items,
        }

    def _list_artist_albums(self, artist_id):
        c = self._conn.cursor()
        artist = c.execute("SELECT name FROM album_artist WHERE id = ?", (artist_id,)).fetchone()
        if not artist:
            return None
        rows = c.execute("""
            SELECT a.id, a.title, a.release_year, a.large_cover_url, a.container_id,
                   COUNT(t.id) as track_count
            FROM album a
            LEFT JOIN track t ON t.album_id = a.id
            WHERE a.album_artist_id = ?
            GROUP BY a.id
            ORDER BY a.release_year, a.normalized_title
        """, (artist_id,)).fetchall()
        items = []
        for r in rows:
            items.append({
                "type": "album",
                "name": r['title'],
                "id": str(r['id']),
                "path": f"albums/{r['id']}",
                "year": r['release_year'],
                "track_count": r['track_count'],
                "artwork": self._album_art.get(r['id']) is not None,
                "artist": artist['name'],
            })
        return {
            "path": f"artists/{artist_id}",
            "parent": "artists",
            "name": artist['name'],
            "items": items,
        }

    def _browse_albums(self, rest):
        if not rest:
            return self._list_all_albums()
        return self._list_album_tracks(rest)

    def _list_all_albums(self):
        c = self._conn.cursor()
        rows = c.execute("""
            SELECT a.id, a.title, a.release_year, a.large_cover_url, a.container_id,
                   aa.name as artist_name,
                   COUNT(t.id) as track_count
            FROM album a
            LEFT JOIN album_artist aa ON a.album_artist_id = aa.id
            LEFT JOIN track t ON t.album_id = a.id
            GROUP BY a.id
            ORDER BY a.normalized_title
        """).fetchall()
        items = []
        for r in rows:
            items.append({
                "type": "album",
                "name": r['title'],
                "id": str(r['id']),
                "path": f"albums/{r['id']}",
                "year": r['release_year'],
                "track_count": r['track_count'],
                "artwork": self._album_art.get(r['id']) is not None,
                "artist": r['artist_name'],
            })
        return {
            "path": "albums",
            "parent": "",
            "name": "Albums",
            "items": items,
        }

    def _list_album_tracks(self, album_id):
        c = self._conn.cursor()
        album = c.execute("""
            SELECT a.id, a.title, a.release_year, a.large_cover_url, a.container_id,
                   aa.name as artist_name
            FROM album a
            LEFT JOIN album_artist aa ON a.album_artist_id = aa.id
            WHERE a.id = ?
        """, (album_id,)).fetchone()
        if not album:
            return None
        rows = c.execute("""
            SELECT id, title, index_, duration, track_artist_normalized_name, url, genre
            FROM track
            WHERE album_id = ?
            ORDER BY index_
        """, (album_id,)).fetchall()
        items = []
        for r in rows:
            items.append({
                "type": "track",
                "name": r['title'] or f"Track {r['index_']}",
                "id": str(r['id']),
                "path": f"albums/{album_id}/{r['id']}",
                "index": r['index_'],
                "track_number": r['index_'],
                "duration": r['duration'],
                "artist": r['track_artist_normalized_name'] or album['artist_name'],
                "album_id": str(album_id),
            })
        return {
            "path": f"albums/{album_id}",
            "parent": "albums",
            "name": album['title'],
            "items": items,
        }

    def _browse_genres(self, rest):
        if not rest:
            return self._list_genres()
        return self._list_genre_albums(urllib.parse.unquote(rest))

    def _list_genres(self):
        c = self._conn.cursor()
        rows = c.execute("""
            SELECT genre, COUNT(DISTINCT album_id) as album_count
            FROM track
            WHERE genre IS NOT NULL AND genre != ''
            GROUP BY genre
            ORDER BY genre
        """).fetchall()
        items = []
        for r in rows:
            genre_path = f"genres/{urllib.parse.quote(r['genre'], safe='')}"
            items.append({
                "type": "category",
                "name": r['genre'],
                "id": genre_path,
                "path": genre_path,
                "icon": "music-notes-simple",
                "count": r['album_count'],
            })
        return {
            "path": "genres",
            "parent": "",
            "name": "Genres",
            "items": items,
        }

    def _list_genre_albums(self, genre_name):
        c = self._conn.cursor()
        rows = c.execute("""
            SELECT DISTINCT a.id, a.title, a.release_year, a.large_cover_url, a.container_id,
                   aa.name as artist_name,
                   (SELECT COUNT(*) FROM track t2 WHERE t2.album_id = a.id) as track_count
            FROM track t
            JOIN album a ON t.album_id = a.id
            LEFT JOIN album_artist aa ON a.album_artist_id = aa.id
            WHERE t.genre = ?
            ORDER BY a.normalized_title
        """, (genre_name,)).fetchall()
        items = []
        for r in rows:
            items.append({
                "type": "album",
                "name": r['title'],
                "id": str(r['id']),
                "path": f"albums/{r['id']}",
                "year": r['release_year'],
                "track_count": r['track_count'],
                "artwork": self._album_art.get(r['id']) is not None,
                "artist": r['artist_name'],
            })
        return {
            "path": f"genres/{urllib.parse.quote(genre_name, safe='')}",
            "parent": "genres",
            "name": genre_name,
            "items": items,
        }

    def _browse_folders(self, rest):
        """Filesystem browse under the music root."""
        target = self.music_root / rest if rest else self.music_root
        target = target.resolve()
        if not target.is_relative_to(self.music_root):
            return None
        if not target.is_dir():
            return None
        return _list_real_dir(target, f"folders/{rest}" if rest else "folders",
                              "folders/" + str(target.parent.relative_to(self.music_root))
                              if target != self.music_root else "",
                              target.name if rest else "Folders")

    # -- Track lookup --

    def get_track(self, track_id):
        """Get a single track by ID with full metadata."""
        c = self._conn.cursor()
        row = c.execute("""
            SELECT t.id, t.title, t.index_, t.duration,
                   t.track_artist_normalized_name as artist, t.url, t.genre,
                   a.id as album_id, a.title as album_title, a.release_year as year,
                   a.large_cover_url, a.container_id,
                   aa.name as album_artist
            FROM track t
            JOIN album a ON t.album_id = a.id
            LEFT JOIN album_artist aa ON a.album_artist_id = aa.id
            WHERE t.id = ?
        """, (track_id,)).fetchone()
        if not row:
            return None
        return dict(row)

    def get_album_tracks(self, album_id):
        """Get all tracks in an album, ordered by index."""
        c = self._conn.cursor()
        rows = c.execute("""
            SELECT t.id, t.title, t.index_, t.duration,
                   t.track_artist_normalized_name as artist, t.url, t.genre,
                   a.id as album_id, a.title as album_title, a.release_year as year,
                   a.large_cover_url, a.container_id,
                   aa.name as album_artist
            FROM track t
            JOIN album a ON t.album_id = a.id
            LEFT JOIN album_artist aa ON a.album_artist_id = aa.id
            WHERE t.album_id = ?
            ORDER BY t.index_
        """, (album_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_track_file_path(self, track_id):
        """Get the local filesystem path for a track."""
        c = self._conn.cursor()
        row = c.execute("SELECT url FROM track WHERE id = ?", (track_id,)).fetchone()
        if not row or not row['url']:
            return None
        return self._translate_path(row['url'])

    def get_album_artwork_path(self, album_id):
        """Get the artwork file path for an album."""
        try:
            return self._album_art[int(album_id)]
        except (KeyError, ValueError, TypeError):
            pass
        # Fallback: query + disk check (uncached album_id)
        c = self._conn.cursor()
        row = c.execute(
            "SELECT large_cover_url, container_id FROM album WHERE id = ?",
            (album_id,)
        ).fetchone()
        if not row:
            return None
        return self._album_artwork_path(row)

    def get_artist_artwork_path(self, artist_id):
        """Get artwork path for an artist (first album's cover)."""
        try:
            return self._artist_art[int(artist_id)]
        except (KeyError, ValueError, TypeError):
            pass
        # Fallback: query + disk check (uncached artist_id)
        c = self._conn.cursor()
        row = c.execute("""
            SELECT large_cover_url, container_id FROM album
            WHERE album_artist_id = ?
            ORDER BY release_year LIMIT 1
        """, (artist_id,)).fetchone()
        if not row:
            return None
        return self._album_artwork_path(row)
