"""Plain filesystem browsing for USB drives."""

import logging
from pathlib import Path

from lib.file_playback import AUDIO_EXTENSIONS, ARTWORK_NAMES, ARTWORK_EXTS

log = logging.getLogger('beo-usb')


def _find_artwork(dir_path):
    """Find artwork image in directory (case-insensitive)."""
    try:
        names = {e.name.lower(): e for e in dir_path.iterdir() if e.is_file()}
    except OSError:
        return None
    for art_name in ARTWORK_NAMES:
        for ext in ARTWORK_EXTS:
            key = f"{art_name}{ext}"
            if key in names:
                return names[key]
    return None


def _list_real_dir(dir_path, rel_path, parent, name=None):
    """List contents of an actual filesystem directory."""
    folders = []
    files = []
    audio_index = 0
    try:
        entries = sorted(dir_path.iterdir(), key=lambda e: e.name.lower())
    except OSError as e:
        log.error("Cannot list %s: %s", dir_path, e)
        return None
    for entry in entries:
        if entry.name.startswith('.'):
            continue
        if entry.is_dir():
            child_rel = f"{rel_path}/{entry.name}" if rel_path else entry.name
            folders.append({
                "type": "folder",
                "name": entry.name,
                "path": child_rel,
                "artwork": _find_artwork(entry) is not None,
            })
        elif entry.is_file() and entry.suffix.lower() in AUDIO_EXTENSIONS:
            child_rel = f"{rel_path}/{entry.name}" if rel_path else entry.name
            files.append({
                "type": "file",
                "name": entry.name,
                "path": child_rel,
                "index": audio_index,
            })
            audio_index += 1
    return {
        "path": rel_path,
        "parent": parent,
        "name": name or dir_path.name,
        "artwork": _find_artwork(dir_path) is not None,
        "items": folders + files,
    }


class FileBrowser:
    """Stateless directory listing for a single root path."""

    def __init__(self, root_path, name="USB"):
        self.name = name
        self.root = Path(root_path).resolve() if root_path else None
        if self.root and self.root.is_dir():
            log.info("FileBrowser root: %s", self.root)
        else:
            log.warning("FileBrowser root not found: %s", root_path)
            self.root = None

    @property
    def available(self):
        return self.root is not None and self.root.is_dir()

    def browse(self, path=""):
        if not self.available:
            return None
        if not path:
            return _list_real_dir(self.root, "", None, self.name)
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root) or not target.exists():
            return None
        if not target.is_dir():
            return None
        parent_rel = str(target.parent.relative_to(self.root))
        parent = parent_rel if parent_rel != '.' else ""
        return _list_real_dir(target, path, parent)

    def find_artwork_path(self, rel_path):
        if not self.available:
            return None
        if not rel_path:
            return _find_artwork(self.root)
        target = (self.root / rel_path).resolve()
        if not target.is_relative_to(self.root):
            return None
        if target.is_dir():
            return _find_artwork(target)
        return None

    def get_audio_files(self, rel_path):
        if not self.available:
            return []
        target = (self.root / rel_path).resolve() if rel_path else self.root
        if not target.is_relative_to(self.root) or not target.is_dir():
            return []
        try:
            entries = sorted(target.iterdir(), key=lambda e: e.name.lower())
        except OSError:
            return []
        return [e for e in entries if e.is_file() and e.suffix.lower() in AUDIO_EXTENSIONS]

    def resolve_file(self, rel_path):
        """Resolve a relative path to a real file. Returns Path or None."""
        if not self.available or not rel_path:
            return None
        target = (self.root / rel_path).resolve()
        if target.is_relative_to(self.root) and target.is_file():
            return target
        return None
