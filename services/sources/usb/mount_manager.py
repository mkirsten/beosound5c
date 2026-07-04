"""MountManager -- auto-detect and mount BM5 drives."""

import asyncio
import json
import sqlite3
import logging
import re
from pathlib import Path

from bm5_library import BM5Library
from file_browser import FileBrowser

log = logging.getLogger('beo-usb')


class MountManager:
    """Manages USB mount points from config. Auto-detects BM5 drives."""

    def __init__(self, paths):
        self.paths = paths or []
        self.mounts = []  # list of (name, browser) -- BM5Library or FileBrowser

    async def init(self):
        """Initialize mounts from configured paths, then auto-detect if needed.

        A single unreadable path must never kill the service: a failing
        or yanked disk surfaces as OSError EIO from os.stat — pathlib's
        is_dir() only swallows does-not-exist errors, everything else
        propagates.  Treat such paths as "no USB present" and move on.
        """
        for path in self.paths:
            try:
                browser = await self._init_path(path)
            except (OSError, sqlite3.Error) as e:
                log.warning("Skipping unreadable path %s: %s", path, e)
                continue
            if browser:
                name = getattr(browser, 'name', Path(path).name)
                self.mounts.append((name, browser))

        # No configured paths, or none worked — auto-detect BM5 HDD
        if not any(isinstance(b, BM5Library) for _, b in self.mounts):
            try:
                browser = await self._auto_detect_bm5()
            except (OSError, sqlite3.Error) as e:
                log.warning("USB auto-detect failed: %s", e)
                browser = None
            if browser:
                self.mounts.append((browser.name, browser))

    async def _init_path(self, path):
        """Initialize a single path as BM5Library or FileBrowser.
        If the path is an empty directory, try mounting an NTFS partition there."""
        p = Path(path)
        if not p.is_dir():
            log.warning("Path not found: %s", path)
            return None

        # Empty dir — likely an unmounted mount point
        if not any(p.iterdir()):
            if not await self._try_mount_to(path):
                log.warning("Empty mount point, no NTFS partition found: %s", path)
                return None

        # Check for BM5 drive
        if (p / "BM-Share" / "Music").is_dir():
            lib = BM5Library(path)
            if lib.available:
                lib.open()
                log.info("BM5 library at %s: %s", path, lib.name)
                return lib

        # Plain filesystem
        browser = FileBrowser(path)
        if browser.available:
            return browser
        return None

    async def _try_mount_to(self, target_path):
        """Try to mount an unmounted NTFS partition at target_path."""
        try:
            result = await asyncio.create_subprocess_exec(
                'lsblk', '--json', '-o', 'NAME,FSTYPE,MOUNTPOINT,SIZE',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await result.communicate()
            data = json.loads(stdout)
        except Exception as e:
            log.warning("lsblk failed: %s", e)
            return False

        candidates = []
        for dev in data.get('blockdevices', []):
            self._collect_ntfs(dev, candidates)

        for dev_name, mountpoint in candidates:
            if mountpoint:
                continue
            dev_path = f"/dev/{dev_name}"
            for fs in ('ntfs3', 'ntfs-3g'):
                proc = await asyncio.create_subprocess_exec(
                    'sudo', 'mount', '-t', fs, '-o', 'ro,uid=1000,gid=1000',
                    dev_path, target_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
                if proc.returncode == 0:
                    log.info("Mounted %s at %s (%s)", dev_path, target_path, fs)
                    return True
        return False

    async def _auto_detect_bm5(self):
        """Scan block devices for NTFS partition with BM5 marker."""
        mount_target = "/mnt/beo-usb-auto"

        # Check if already mounted at target from a previous run
        try:
            already_mounted = (Path(mount_target).is_dir()
                               and (Path(mount_target) / "BM-Share" / "Music").is_dir())
        except OSError as e:
            # Stale mount from a failing/yanked disk (EIO on stat).
            # Lazy-unmount it so a later re-plug can mount cleanly,
            # then fall through to fresh block-device detection.
            log.warning("Mount at %s unreadable (%s) — releasing stale mount",
                        mount_target, e)
            proc = await asyncio.create_subprocess_exec(
                'sudo', 'umount', '-l', mount_target,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            already_mounted = False
        if already_mounted:
            lib = BM5Library(mount_target)
            if lib.available:
                lib.open()
                log.info("BM5 already mounted at %s", mount_target)
                return lib

        try:
            result = await asyncio.create_subprocess_exec(
                'lsblk', '--json', '-o', 'NAME,FSTYPE,MOUNTPOINT,SIZE',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await result.communicate()
            data = json.loads(stdout)
        except Exception as e:
            log.warning("lsblk failed: %s", e)
            return None

        candidates = []
        for dev in data.get('blockdevices', []):
            self._collect_ntfs(dev, candidates)

        for dev_name, mountpoint in candidates:
            # Already mounted somewhere — check for BM5 marker
            if mountpoint:
                if (Path(mountpoint) / "BM-Share" / "Music").is_dir():
                    lib = BM5Library(mountpoint)
                    if lib.available:
                        lib.open()
                        log.info("BM5 found at existing mount: %s", mountpoint)
                        return lib
                continue

            # Probe unmounted partition
            dev_path = f"/dev/{dev_name}"
            tmp_mount = f"/tmp/beo-usb-probe-{dev_name}"
            try:
                Path(tmp_mount).mkdir(parents=True, exist_ok=True)
                mounted = False
                for fs in ('ntfs3', 'ntfs-3g'):
                    proc = await asyncio.create_subprocess_exec(
                        'sudo', 'mount', '-t', fs, '-o', 'ro',
                        dev_path, tmp_mount,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()
                    if proc.returncode == 0:
                        mounted = True
                        break
                if not mounted:
                    continue

                if (Path(tmp_mount) / "BM-Share" / "Music").is_dir():
                    # Found BM5 — remount at permanent location
                    await self._umount(tmp_mount)
                    await asyncio.create_subprocess_exec(
                        'sudo', 'mkdir', '-p', mount_target,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    for fs in ('ntfs3', 'ntfs-3g'):
                        proc = await asyncio.create_subprocess_exec(
                            'sudo', 'mount', '-t', fs, '-o', 'ro',
                            dev_path, mount_target,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await proc.wait()
                        if proc.returncode == 0:
                            lib = BM5Library(mount_target)
                            if lib.available:
                                lib.open()
                                log.info("BM5 auto-mounted: %s -> %s", dev_path, mount_target)
                                return lib
                else:
                    await self._umount(tmp_mount)
            except Exception as e:
                log.warning("Probe failed for %s: %s", dev_path, e)
            finally:
                try:
                    Path(tmp_mount).rmdir()
                except OSError:
                    pass

        return None

    async def _umount(self, path):
        await asyncio.create_subprocess_exec(
            'sudo', 'umount', path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    def _collect_ntfs(self, dev, candidates):
        """Recursively collect NTFS partitions from lsblk output."""
        fstype = dev.get('fstype', '')
        if fstype and 'ntfs' in fstype.lower():
            candidates.append((dev['name'], dev.get('mountpoint')))
        for child in dev.get('children', []):
            self._collect_ntfs(child, candidates)

    @property
    def available(self):
        return len(self.mounts) > 0

    def get_mount(self, index):
        if 0 <= index < len(self.mounts):
            return self.mounts[index]
        return None, None

    def find_bm5(self, index=0):
        """Find the first (or nth) BM5Library mount."""
        count = 0
        for name, browser in self.mounts:
            if isinstance(browser, BM5Library):
                if count == index:
                    return browser
                count += 1
        return None
