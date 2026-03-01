"""On-the-fly audio transcoding with RAM/SSD caching."""

import asyncio
import hashlib
import logging
import shutil
import time
from pathlib import Path

from .constants import PASSTHROUGH_SETS, TRANSCODE_CODECS

log = logging.getLogger('beo-usb')


class TranscodeCache:
    """On-the-fly audio transcoding with RAM/SSD caching.

    target_format: 'mp3' (Sonos default) or 'flac' (Bluesound lossless).
    """

    def __init__(self, target_format='mp3', max_bytes=300 * 1024 * 1024):
        self.target_format = target_format
        self.max_bytes = max_bytes
        self._cache_dir = None
        self._lock = asyncio.Lock()
        self._active_transcodes = {}  # path -> asyncio.Event (signals completion)

    def init(self):
        """Set up cache directory. Prefer tmpfs, fall back to /tmp."""
        shm = Path("/dev/shm")
        if shm.is_dir():
            self._cache_dir = shm / "beo-usb-transcode"
        else:
            self._cache_dir = Path("/tmp/beo-usb-transcode")
        # Wipe on start
        if self._cache_dir.exists():
            shutil.rmtree(self._cache_dir, ignore_errors=True)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        log.info("Transcode cache: %s (max %dMB)", self._cache_dir, self.max_bytes // (1024 * 1024))

    def _cache_key(self, file_path):
        return hashlib.sha256(file_path.encode()).hexdigest()

    def _cached_path(self, file_path):
        return self._cache_dir / f"{self._cache_key(file_path)}.{self.target_format}"

    def needs_transcode(self, file_path):
        """Check if a file needs transcoding for the target player."""
        ext = Path(file_path).suffix.lower()
        return ext not in PASSTHROUGH_SETS.get(self.target_format, PASSTHROUGH_SETS['mp3'])

    async def get_or_transcode(self, file_path):
        """Return path to a streamable file. Transcodes WMA->FLAC if needed."""
        if not self.needs_transcode(file_path):
            return file_path

        cached = self._cached_path(file_path)
        if cached.is_file():
            # Touch for LRU
            cached.touch()
            return str(cached)

        async with self._lock:
            # Check if another request is already transcoding this file
            if file_path in self._active_transcodes:
                event = self._active_transcodes[file_path]
                # Release lock while waiting for transcode to finish
                self._lock.release()
                try:
                    await event.wait()
                finally:
                    await self._lock.acquire()
                if cached.is_file():
                    return str(cached)
                return None

            # Re-check cache after acquiring lock
            if cached.is_file():
                cached.touch()
                return str(cached)

            event = asyncio.Event()
            self._active_transcodes[file_path] = event

        # Transcode outside lock
        try:
            await self._transcode(file_path, str(cached))
            await self._evict_if_needed()
            return str(cached) if cached.is_file() else None
        finally:
            event.set()
            self._active_transcodes.pop(file_path, None)

    async def _transcode(self, input_path, output_path):
        """Run ffmpeg to transcode to the target format (mp3 or flac)."""
        codec_args = TRANSCODE_CODECS.get(self.target_format, TRANSCODE_CODECS['mp3'])
        log.info("Transcoding -> %s: %s", self.target_format, Path(input_path).name)
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg', '-y', '-i', input_path,
            *codec_args, output_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        elapsed = time.monotonic() - start
        if proc.returncode == 0:
            size = Path(output_path).stat().st_size / (1024 * 1024)
            log.info("Transcoded in %.1fs (%.1fMB): %s", elapsed, size, Path(input_path).name)
        else:
            log.error("Transcode failed (%d): %s", proc.returncode, stderr.decode()[-200:])

    async def _evict_if_needed(self):
        """LRU eviction if cache exceeds max size."""
        if not self._cache_dir:
            return
        files = sorted(self._cache_dir.glob(f"*.{self.target_format}"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        while total > self.max_bytes and files:
            victim = files.pop(0)
            total -= victim.stat().st_size
            victim.unlink(missing_ok=True)
            log.info("Evicted from cache: %s", victim.name)

    async def prefetch(self, file_paths):
        """Pre-transcode a list of files in the background."""
        for fp in file_paths:
            if self.needs_transcode(fp):
                cached = self._cached_path(fp)
                if not cached.is_file():
                    asyncio.create_task(self.get_or_transcode(fp))

    def cleanup(self):
        if self._cache_dir and self._cache_dir.exists():
            shutil.rmtree(self._cache_dir, ignore_errors=True)
