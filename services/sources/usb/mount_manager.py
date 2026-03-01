"""MountManager -- auto-detect and mount BM5 drives."""

import asyncio
import json
import logging
import re
from pathlib import Path

from bm5_library import BM5Library
from file_browser import FileBrowser

log = logging.getLogger('beo-usb')


class MountManager:
    """Manages USB mount points from config. Auto-detects BM5 drives."""

    def __init__(self, mounts_config):
        self.config = mounts_config or []
        self.mounts = []  # list of (name, browser) -- BM5Library or FileBrowser

    async def init(self):
        """Initialize all mounts from config."""
        for i, mc in enumerate(self.config):
            name = mc.get('name', f'USB {i+1}')
            mount_type = mc.get('type', 'plain')
            path = mc.get('path')

            if mount_type == 'bm5':
                browser = await self._init_bm5(name, path)
            else:
                browser = FileBrowser(path, name) if path else None

            if browser:
                self.mounts.append((name, browser))
                log.info("Mount [%d] %s: %s (%s)", i, name,
                         type(browser).__name__,
                         getattr(browser, 'mount', getattr(browser, 'root', '?')))
            else:
                log.warning("Mount [%d] %s: not available", i, name)

    async def _init_bm5(self, name, manual_path=None):
        """Initialize a BM5 library. Auto-detect if no path given."""
        if manual_path:
            lib = BM5Library(manual_path, name)
            if lib.available:
                lib.open()
                return lib
            log.warning("BM5 manual path not available: %s", manual_path)
            return None

        # Auto-detect: scan for NTFS partitions with BM5 marker
        mount_point = await self._auto_detect_bm5(name)
        if mount_point:
            lib = BM5Library(mount_point, name)
            if lib.available:
                lib.open()
                return lib
        return None

    async def _auto_detect_bm5(self, name):
        """Scan block devices for NTFS partition with BM5 marker."""
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
        mount_target = f"/mnt/beo-usb-{slug}"

        # Check if already mounted at target
        if Path(mount_target).is_dir() and (Path(mount_target) / "BM-Share" / "Music").is_dir():
            log.info("BM5 already mounted at %s", mount_target)
            return mount_target

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
            # If already mounted somewhere, check for marker
            if mountpoint:
                marker = Path(mountpoint) / "BM-Share" / "Music"
                if marker.is_dir():
                    log.info("BM5 found at existing mount: %s", mountpoint)
                    return mountpoint
                continue

            # Try temp-mounting to check
            dev_path = f"/dev/{dev_name}"
            tmp_mount = f"/tmp/beo-usb-probe-{dev_name}"
            try:
                Path(tmp_mount).mkdir(parents=True, exist_ok=True)
                proc = await asyncio.create_subprocess_exec(
                    'sudo', 'mount', '-t', 'ntfs3', '-o', 'ro',
                    dev_path, tmp_mount,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
                if proc.returncode != 0:
                    # Try ntfs-3g fallback
                    proc = await asyncio.create_subprocess_exec(
                        'sudo', 'mount', '-t', 'ntfs-3g', '-o', 'ro',
                        dev_path, tmp_mount,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()

                marker = Path(tmp_mount) / "BM-Share" / "Music"
                if marker.is_dir():
                    # Found! Move to permanent mount point
                    await asyncio.create_subprocess_exec(
                        'sudo', 'umount', tmp_mount,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    proc = await asyncio.create_subprocess_exec(
                        'sudo', 'mkdir', '-p', mount_target,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()
                    proc = await asyncio.create_subprocess_exec(
                        'sudo', 'mount', '-t', 'ntfs3', '-o', 'ro',
                        dev_path, mount_target,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()
                    if proc.returncode != 0:
                        proc = await asyncio.create_subprocess_exec(
                            'sudo', 'mount', '-t', 'ntfs-3g', '-o', 'ro',
                            dev_path, mount_target,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await proc.wait()
                    log.info("BM5 auto-mounted: %s -> %s", dev_path, mount_target)
                    return mount_target
                else:
                    await asyncio.create_subprocess_exec(
                        'sudo', 'umount', tmp_mount,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
            except Exception as e:
                log.warning("Probe failed for %s: %s", dev_path, e)
            finally:
                # Clean up tmp mount dir
                try:
                    Path(tmp_mount).rmdir()
                except OSError:
                    pass

        log.warning("No BM5 drive found")
        return None

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
