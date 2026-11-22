#!/usr/bin/env python3
"""
BeoSound 5c B&O ASE Player (beo-player-ase) — EXPERIMENTAL

Drives an older Bang & Olufsen networked speaker on the ASE ("SoundCenter")
platform — the pre-Mozart generation: BeoPlay A6/A9 (gen2), BeoSound
1/2/35 (gen1), BeoSound Core/Essence/Moment, BeoPlay M5, and the BeoLink
Converter NL/ML. These speak B&O's BeoNetRemote (BNR) HTTP API, not the
Mozart Open API.

  REST base: http://<ip>:8080   (paths accepted with or without a trailing slash)
  Volume:    GET/PUT /BeoZone/Zone/Sound/Volume/Speaker/Level  {"level": N}   N in the
             product's own range — 0–90 on every ASE product seen so far, not 0–100
  Transport: POST /BeoZone/Zone/Stream/{Play|Pause|Stop|Forward|Backward}      (streaming sources)
             POST /BeoZone/Zone/List/{StepUp|StepDown}                        (legacy sources: CD, radio, aux …)
  Standby:   PUT  /BeoDevice/powerManagement/standby {"standby":{"powerState":"standby"}}
  Now playing: GET /BeoNotify/Notifications — a chunked stream of one JSON
             object per line: {"notification":{"type":..,"kind":..,"data":..}}.
             Types used here: VOLUME, SOURCE, PROGRESS_INFORMATION,
             NOW_PLAYING_STORED_MUSIC, NOW_PLAYING_NET_RADIO,
             NOW_PLAYING_LEGACY, NUMBER_AND_NAME, SHUTDOWN.

Where this comes from: the B&O app v7.6.2 decompiled (private/apk/FINDINGS.md)
and watched live against an emulated ASE product (bs9000-gateway's bo-emulator/ —
"what the B&O app actually does"), plus a real BeoLink Converter NL/ML probed
on the LAN (private/blc-nlml-bs9000-re.md). It has not yet been run against
an ASE *speaker*. Beolink multiroom on ASE is NOT wired — standalone player
only (no SPEAKERS).

BLC gotcha worth knowing: on a BeoLink Converter, Stream/* commands return
200 but are silently dropped unless the converter owns the source session,
i.e. the source was activated through the API (POST /BeoZone/Zone/ActiveSources)
rather than at the deck.
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
# A playing product pushes PROGRESS_INFORMATION every second; an idle one can
# go quiet for a long time. Only the read gap is bounded — never the whole
# stream, or a healthy long-poll would be cut and re-primed every N seconds.
NOTIFY_READ_TIMEOUT = 90
DEFAULT_VOLUME_RANGE_MAX = 90

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('beo-player-ase')


# ── Pure helpers over BNR JSON (unit-tested) ──

# Source ids the B&O app drives with List/StepUp|StepDown instead of
# Stream/Forward|Backward: the legacy (non-streaming) sources. Ids are
# "<ID>:<jid>"; the app matches on the id prefix.
LEGACY_SOURCE_PREFIXES = ("CD", "AUX", "LINEIN", "PHONO", "RADIO", "TUNER",
                          "TP", "A.MEM", "DVD", "TV")

_BNR_STATES = {"play": "playing", "playing": "playing",
               "pause": "paused", "paused": "paused"}
_IMG_RANK = {"small": 1, "medium": 2, "large": 3}


def unwrap_notification(msg) -> tuple[str, dict]:
    """``{"notification": {type, data, ...}}`` -> (TYPE, data)."""
    note = msg.get("notification") if isinstance(msg, dict) else None
    if not isinstance(note, dict):
        note = msg if isinstance(msg, dict) else {}
    data = note.get("data")
    return str(note.get("type", "")).upper(), data if isinstance(data, dict) else {}


def is_legacy_source(source_id) -> bool:
    sid = str(source_id or "").split(":", 1)[0].upper()
    return sid.startswith(LEGACY_SOURCE_PREFIXES)


def pick_image_url(images) -> str:
    """Largest of a trackImage[]/image[] list ({url, size: small|medium|large})."""
    if not isinstance(images, list):
        return ""
    best, best_rank = "", -1
    for img in images:
        if not isinstance(img, dict) or not img.get("url"):
            continue
        rank = _IMG_RANK.get(str(img.get("size", "")).lower(), 0)
        if rank >= best_rank:
            best, best_rank = img["url"], rank
    return best


def playback_state_from(ntype: str, data: dict) -> str | None:
    """BS5c state carried by a notification, or None if it carries none.

    PROGRESS_INFORMATION and NOW_PLAYING_LEGACY carry ``state`` (play|pause|
    stop); SOURCE carries it inside primaryExperience; SHUTDOWN means the
    product went to standby."""
    if ntype == "SHUTDOWN":
        return "stopped"
    raw = data.get("state")
    if raw is None and ntype == "SOURCE":
        raw = (data.get("primaryExperience") or {}).get("state")
    if raw is None:
        return None
    return _BNR_STATES.get(str(raw).lower(), "stopped")


def media_from_notification(ntype: str, data: dict) -> dict | None:
    """Now-playing fields (title/artist/album/art_url/duration) for the
    NOW_PLAYING_* / NUMBER_AND_NAME frames, or None for anything else."""
    if ntype == "NOW_PLAYING_STORED_MUSIC":
        return {"title": data.get("name") or "", "artist": data.get("artist") or "",
                "album": data.get("album") or "",
                "art_url": pick_image_url(data.get("trackImage")),
                "duration": data.get("duration")}
    if ntype == "NOW_PLAYING_NET_RADIO":
        return {"title": data.get("name") or "",
                "artist": data.get("liveDescription") or "",
                "album": "", "art_url": pick_image_url(data.get("image")),
                "duration": None}
    if ntype == "NUMBER_AND_NAME":
        # TV channel / legacy list entry: number + name
        number = data.get("number")
        return {"title": data.get("name") or "", "artist": f"{number}" if number else "",
                "album": "", "art_url": "", "duration": None}
    if ntype == "NOW_PLAYING_LEGACY":
        # CD / tape behind a BeoLink Converter: only a track number is known.
        track = data.get("trackNumber")
        if not track:
            return None
        return {"title": f"Track {track}", "artist": "", "album": "",
                "art_url": "", "duration": None}
    return None


def volume_percent(data: dict, default_max: int = DEFAULT_VOLUME_RANGE_MAX) -> tuple[int | None, int]:
    """(percent, range_max) from a VOLUME frame ``{"speaker": {"level", "muted",
    "range": {"minimum", "maximum"}}}``. ASE products run 0–90, so the level
    is scaled to the 0–100 the router works in."""
    speaker = data.get("speaker") if isinstance(data, dict) else None
    if not isinstance(speaker, dict) or speaker.get("level") is None:
        return None, default_max
    try:
        range_max = int((speaker.get("range") or {}).get("maximum") or default_max) or default_max
        level = int(speaker["level"])
    except (TypeError, ValueError):
        return None, default_max
    return max(0, min(100, round(level * 100 / range_max))), range_max


def active_source_id(data: dict) -> str:
    """Source id out of a SOURCE frame: ``primary`` or primaryExperience.source.id."""
    if not isinstance(data, dict):
        return ""
    return (data.get("primary")
            or ((data.get("primaryExperience") or {}).get("source") or {}).get("id")
            or "")


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
        self._active_source = ""          # "<ID>:<jid>" from the SOURCE frames
        self._volume_range_max = DEFAULT_VOLUME_RANGE_MAX

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
            logger.warning("ASE POST %s failed: %s", path, e or type(e).__name__)
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
        if is_legacy_source(self._active_source):
            return await self._post("/BeoZone/Zone/List/StepUp")
        return await self._post("/BeoZone/Zone/Stream/Forward")

    async def prev_track(self) -> bool:
        if is_legacy_source(self._active_source):
            return await self._post("/BeoZone/Zone/List/StepDown")
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
            "active_source": self._active_source,
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
        """Read /BeoNotify/Notifications for now-playing, progress, source and
        volume. On connect the product sends a snapshot (SOURCE, VOLUME,
        NOW_PLAYING_*, PROGRESS_INFORMATION), then live frames."""
        consecutive_failures = 0
        while self.running:
            try:
                async with self._http_session.get(
                    f"{self._base}/BeoNotify/Notifications",
                    timeout=aiohttp.ClientTimeout(
                        total=None, sock_connect=10,
                        sock_read=NOTIFY_READ_TIMEOUT)) as resp:
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
                    logger.error("ASE notify stream failed (unreachable?): %s",
                                 e or type(e).__name__)
                elif consecutive_failures % 20 == 0:
                    logger.warning("ASE still unreachable (%d attempts)",
                                   consecutive_failures)
                await asyncio.sleep(min(2 ** min(consecutive_failures, 4), 30.0))

    async def _handle_notification(self, msg: dict):
        ntype, data = unwrap_notification(msg)

        if ntype == "SOURCE":
            sid = active_source_id(data)
            if sid and sid != self._active_source:
                self._active_source = sid
                logger.info("Active source: %s", sid)

        st = playback_state_from(ntype, data)
        if st is not None and st != self._current_playback_state:
            self._current_playback_state = st
            if st == "playing":
                self._spawn(self.trigger_wake(), name="trigger_wake")
            if self._cached_media_data:
                self._cached_media_data["state"] = st
                await self.broadcast_media_update(
                    self._cached_media_data, "state_change")

        if ntype == "PROGRESS_INFORMATION" and self._cached_media_data:
            self._cached_media_data["position"] = self._sec_to_time(data.get("position"))
            if data.get("totalDuration"):
                self._cached_media_data["duration"] = self._sec_to_time(data.get("totalDuration"))

        media = media_from_notification(ntype, data)
        if media and media["title"]:
            track_id = f"{media['title']}|{media['artist']}|{media['album']}"
            if track_id != self._current_track_id:
                self._current_track_id = track_id
                artwork_b64 = None
                if media["art_url"]:
                    result = await self.fetch_artwork(media["art_url"], session=self._http_session)
                    if result:
                        artwork_b64 = result["base64"]
                media_data = {
                    "title": media["title"] or "—", "artist": media["artist"] or "—",
                    "album": media["album"] or "—",
                    "artwork": f"data:image/jpeg;base64,{artwork_b64}" if artwork_b64 else None,
                    "position": "0:00",
                    "duration": self._sec_to_time(media["duration"]),
                    "state": self._current_playback_state or "playing",
                    "speaker_ip": self.ip, "uri": "",
                    "timestamp": int(time.time()),
                }
                self._cached_media_data = media_data
                await self.broadcast_media_update(media_data, "track_change")
                logger.info("Track changed: %s — %s", media["artist"], media["title"])
                if self.seconds_since_command() > USER_ACTION_HORIZON:
                    self._spawn(self.notify_router_playback_override(force=True),
                                name="playback_override")

        if ntype == "VOLUME":
            percent, self._volume_range_max = volume_percent(data, self._volume_range_max)
            if percent is not None:
                self._spawn(self.report_volume_to_router(percent), name="report_volume")

    @staticmethod
    def _sec_to_time(sec) -> str:
        try:
            total = int(sec)
        except (ValueError, TypeError):
            return "0:00"
        if total >= 3600:
            return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"
        return f"{total // 60}:{total % 60:02d}"


async def main():
    player = AsePlayer()
    await player.run()


if __name__ == "__main__":
    asyncio.run(main())
