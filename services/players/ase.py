#!/usr/bin/env python3
"""
BeoSound 5c B&O ASE Player (beo-player-ase) — EXPERIMENTAL / UNVERIFIED

Drives an older Bang & Olufsen networked speaker on the ASE ("SoundCenter")
platform — the pre-Mozart generation: BeoPlay A6/A9 (gen2), BeoSound
1/2/35 (gen1), BeoSound Core, BeoPlay M5, etc. These use the B&O "product"
API (BeoZone / BeoNotify) rather than the Mozart Open API.

  REST base: http://<ip>:8080
  Volume:   GET/PUT /BeoZone/Zone/Sound/Volume/Speaker/Level  {"speaker":{"level":N}}
  Transport: POST /BeoZone/Zone/Stream/{Play|Pause|Stop|Forward|Backward}
  Now playing: GET /BeoNotify/Notifications  (long-poll notification stream)

Unlike Mozart there is no published OpenAPI to validate against, so this is a
best-effort implementation of the documented BeoZone endpoints. **It has not
been run against hardware.** Beolink multiroom on ASE differs from Mozart and
is NOT wired here yet — this ships as a standalone player only (no SPEAKERS),
until the grouping API can be confirmed on a real device.
"""

import asyncio
import json
import logging
import os
import sys
import time

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.active_target import effective_player_ip
from lib.config import cfg
from lib.player_base import PlayerBase
from lib.timings import USER_ACTION_HORIZON

ASE_IP = cfg("player", "ip", default="")
ASE_PORT = 8080
NOTIFY_TIMEOUT = 30

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('beo-player-ase')


class AsePlayer(PlayerBase):
    """B&O ASE / SoundCenter player via the BeoZone/BeoNotify product API."""

    id = "ase"
    name = "Bang & Olufsen"
    port = 8766

    def __init__(self):
        super().__init__()
        self.ip = effective_player_ip()
        self._base = f"http://{self.ip}:{ASE_PORT}"
        self._current_track_id = None
        self._current_playback_state = None

    # ── BeoZone helpers ──

    async def _post(self, path, timeout=6):
        if self._http_session is None or self._http_session.closed:
            return False
        try:
            async with self._http_session.post(
                f"{self._base}{path}",
                timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                resp.raise_for_status()
                return True
        except Exception as e:
            logger.warning("ASE POST %s failed: %s", path, e)
            return False

    # ── PlayerBase abstract methods ──

    async def play(self, uri=None, url=None, track_uri=None, meta=None,
                   radio=False, track_uris=None) -> bool:
        # ASE has no generic "play arbitrary URL" like Mozart; playback is
        # driven by the device's own sources. Best-effort resume.
        if uri or url:
            logger.warning("ASE cannot play external URIs/URLs directly "
                           "(no /playback/uri equivalent)")
        return await self.resume()

    async def pause(self) -> bool:
        return await self._post("/BeoZone/Zone/Stream/Pause")

    async def resume(self) -> bool:
        return await self._post("/BeoZone/Zone/Stream/Play")

    async def next_track(self) -> bool:
        return await self._post("/BeoZone/Zone/Stream/Forward")

    async def prev_track(self) -> bool:
        return await self._post("/BeoZone/Zone/Stream/Backward")

    async def stop(self) -> bool:
        return await self._post("/BeoZone/Zone/Stream/Stop")

    async def get_capabilities(self) -> list:
        # ASE has no "play arbitrary URL" endpoint, so it must NOT advertise
        # url_stream — sources (USB/TIDAL/Plex) key off that to send `url`,
        # which ASE can't honour (silent no-audio). Transport/metadata/volume
        # only, driven by the device's own sources.
        return []

    async def get_track_uri(self) -> str:
        return self._current_track_id or ""

    async def get_status(self) -> dict:
        base = await super().get_status()
        cached = self._cached_media_data or {}
        base.update({
            "speaker_ip": self.ip,
            "state": self._current_playback_state or "stopped",
            "volume": cached.get("volume"),
            "current_track": {
                "title": cached.get("title", "—"),
                "artist": cached.get("artist", "—"),
                "album": cached.get("album", "—"),
            } if cached else None,
            "artwork_cache_size": len(self._artwork_cache),
        })
        return base

    async def on_target_changed(self, ip: str, name: str) -> None:
        # Staged for when Beolink-on-ASE grouping is confirmed: ASE returns
        # supports_grouping() False today, so no /player/target route calls
        # this. Kept correct so enabling grouping later needs no rework.
        self.ip = ip
        self._base = f"http://{ip}:{ASE_PORT}"
        self._current_track_id = None
        logger.info("Retargeted ASE to %s (%s)", name or "?", ip)

    # ── PlayerBase hooks ──

    async def on_start(self):
        if not ASE_IP:
            logger.error("No ASE IP configured (set player.ip in config) — exiting")
            from lib.watchdog import sd_notify
            sd_notify("READY=1\nSTATUS=No player.ip configured, exiting")
            sd_notify("STOPPING=1")
            sys.exit(0)
        logger.info("Starting ASE player for %s (experimental)", self.ip)
        self._monitor_task = self._spawn(self._notify_loop(), name="ase_notify")

    # ── Monitoring (BeoNotify long-poll) ──

    async def _notify_loop(self):
        """Long-poll /BeoNotify/Notifications for now-playing + progress + volume.

        The stream returns newline/whitespace-delimited JSON notification
        objects; parse each and update state. Defensive — the exact envelope
        varies across firmware."""
        consecutive_failures = 0
        while self.running:
            try:
                async with self._http_session.get(
                    f"{self._base}/BeoNotify/Notifications",
                    timeout=aiohttp.ClientTimeout(total=NOTIFY_TIMEOUT + 10)) as resp:
                    resp.raise_for_status()
                    consecutive_failures = 0
                    async for line in resp.content:
                        if not self.running:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except ValueError:
                            continue
                        await self._handle_notification(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                consecutive_failures += 1
                if consecutive_failures == 1:
                    logger.error("ASE notify stream failed (unreachable?): %s", e)
                elif consecutive_failures % 20 == 0:
                    logger.warning("ASE still unreachable (%d attempts)",
                                   consecutive_failures)
                await asyncio.sleep(min(2 ** min(consecutive_failures, 4), 30.0))

    async def _handle_notification(self, msg: dict):
        note = msg.get("notification") or msg
        ntype = str(note.get("type", "")).upper()
        data = note.get("data") or {}

        if "PROGRESS" in ntype or "state" in data:
            raw = str(data.get("state", "")).lower()
            if raw:
                st = "playing" if raw in ("play", "playing") else (
                    "paused" if raw in ("pause", "paused") else "stopped")
                if st != self._current_playback_state:
                    self._current_playback_state = st
                    if st == "playing":
                        self._spawn(self.trigger_wake(), name="trigger_wake")
                    if self._cached_media_data:
                        self._cached_media_data["state"] = st
                        await self.broadcast_media_update(
                            self._cached_media_data, "state_change")

        if "NOW_PLAYING" in ntype or "trackImage" in data or "name" in data:
            title = data.get("name") or data.get("title") or ""
            artist = data.get("artist") or ""
            album = data.get("album") or ""
            track_id = f"{title}|{artist}|{album}"
            if title and track_id != self._current_track_id:
                self._current_track_id = track_id
                imgs = data.get("trackImage") or []
                art_url = ""
                if isinstance(imgs, list) and imgs:
                    art_url = imgs[0].get("url", "")
                artwork_b64 = None
                if art_url:
                    result = await self.fetch_artwork(art_url, session=self._http_session)
                    if result:
                        artwork_b64 = result["base64"]
                media_data = {
                    "title": title or "—", "artist": artist or "—",
                    "album": album or "—",
                    "artwork": f"data:image/jpeg;base64,{artwork_b64}" if artwork_b64 else None,
                    "state": self._current_playback_state or "playing",
                    "speaker_ip": self.ip, "uri": "",
                    "timestamp": int(time.time()),
                }
                self._cached_media_data = media_data
                await self.broadcast_media_update(media_data, "track_change")
                logger.info("Track changed: %s — %s", artist, title)
                if self.seconds_since_command() > USER_ACTION_HORIZON:
                    self._spawn(self.notify_router_playback_override(force=True),
                                name="playback_override")

        if ntype == "VOLUME" or "speaker" in data:
            try:
                level = (data.get("speaker") or {}).get("level")
                if level is not None:
                    self._spawn(self.report_volume_to_router(int(level)),
                                name="report_volume")
            except (ValueError, TypeError):
                pass


async def main():
    player = AsePlayer()
    await player.run()


if __name__ == "__main__":
    asyncio.run(main())
