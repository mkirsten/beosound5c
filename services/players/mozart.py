#!/usr/bin/env python3
"""
BeoSound 5c B&O Mozart Player (beo-player-mozart) — EXPERIMENTAL

Drives a modern Bang & Olufsen speaker on the Mozart platform (Beolab 8/28,
Beosound A5/A9-5thgen/Balance/Emerge/Level, Beoconnect Core, Premiere/Theatre,
"any Mozart-based product post-2020") via B&O's official local REST API.

Mozart Open API: https://github.com/bang-olufsen/mozart-open-api
  REST base: http://<ip>/api/v1   (port 80 — the official mozart-api client
                                   and HA's bang_olufsen integration assume
                                   it; no auth on the local network)
  GET  /playback/state                 — PlaybackState {metadata, progress, source, state:{value}}
  POST /playback/command/{command}     — play | pause | stop | skip | prev  (the full enum)
  POST /playback/uri                   — play an arbitrary URL, body Uri {"location": ...}
  PUT  /sound/volume/level             — VolumeLevel {"level": 0-100}
  PUT  /state/standby                  — network standby
  ws://<ip>:9339/                      — event stream: {"eventType": "WebSocketEvent…", "eventData": …}
                                         (PlaybackMetadata / PlaybackProgress / PlaybackState /
                                         SourceChange / Volume / PowerState)
Multiroom (Beolink):
  GET  /beolink/peers                  — other B&O rooms (friendlyName, ipAddress, jid)
  GET  /beolink/listeners              — rooms currently listening to us
  POST /beolink/join/{jid}             — join a peer's experience
  POST /beolink/expand/{jid}           — pull a peer into ours
  POST /beolink/leave                  — leave

Shapes above are from the OpenAPI (mozart-api.yaml) and the decompiled B&O
app (private/apk/FINDINGS.md), cross-checked with bs9000-gateway's bo-emulator/, which
HA's bang_olufsen integration and the B&O app both talk to. No SSDP/mDNS
needed at runtime: /beolink/peers is the network roster. Not yet validated
against real hardware.
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
from lib.volume_adapters.mozart import volume_level_from_state

MOZART_IP = cfg("player", "ip", default="")
MOZART_PORT = 80
MOZART_WS_PORT = 9339
POLL_INTERVAL = 3.0
POLL_INTERVAL_WS = 30.0   # safety-net poll while the event stream is connected
DEVICE_TIMEOUT = 8        # cap for the cold /player/network fan-out

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('beo-player-mozart')


# ── Pure helpers over Mozart JSON (unit-tested) ──

# RenderingState.value → BS5c playback state. "buffering" counts as playing:
# the product has accepted the stream and the UI should show it as live.
_RENDER_STATES = {
    "started": "playing", "buffering": "playing",
    "paused": "paused",
}


def render_state(state_obj) -> str:
    """PlaybackState.state is RenderingState ``{"value": "started"}``; also
    accept a bare string for tolerance. Anything unknown is ``stopped``."""
    value = state_obj.get("value") if isinstance(state_obj, dict) else state_obj
    return _RENDER_STATES.get(str(value or "").lower(), "stopped")


_ART_RANK = {"small": 1, "medium": 2, "large": 3}


def pick_art_url(art) -> str:
    """Largest image out of a PlaybackContentMetadata.art[] list.

    Streaming sources label entries small/medium/large; net radio keys them
    128x128 … 1024x1024; other sources give a single entry. Prefer the
    largest named or pixel size, else the last entry."""
    if not isinstance(art, list) or not art:
        return ""
    best, best_rank = None, -1
    for entry in art:
        if not isinstance(entry, dict) or not entry.get("url"):
            continue
        size = str(entry.get("size") or entry.get("key") or "").lower()
        rank = _ART_RANK.get(size)
        if rank is None:
            try:
                rank = int(size.split("x", 1)[0]) / 100.0
            except ValueError:
                rank = 0
        if rank >= best_rank:
            best, best_rank = entry, rank
    return best.get("url", "") if best else ""


def metadata_fields(md) -> dict:
    """title/artist/album from PlaybackContentMetadata (artistName/albumName
    per the OpenAPI; the older guesses artist/albumTitle are still accepted)."""
    md = md if isinstance(md, dict) else {}
    return {
        "title": md.get("title") or "",
        "artist": md.get("artistName") or md.get("artist") or "",
        "album": md.get("albumName") or md.get("albumTitle") or "",
        "art_url": pick_art_url(md.get("art")),
    }


def duration_seconds(state) -> int | None:
    """Total duration in seconds: PlaybackProgress.totalDuration (seconds),
    else metadata.totalDurationSeconds, else metadata.totalDuration (ms)."""
    progress = state.get("progress") or {}
    md = state.get("metadata") or {}
    for value, scale in ((progress.get("totalDuration"), 1),
                         (md.get("totalDurationSeconds"), 1),
                         (md.get("totalDuration"), 1000)):
        if value is not None:
            try:
                return int(value) // scale
            except (TypeError, ValueError):
                continue
    return None


def source_blob(src) -> str:
    """Lower-cased id/name/type of a Source for keyword matching. ``type`` is
    ``{"value": ...}`` in the OpenAPI."""
    src = src if isinstance(src, dict) else {}
    stype = src.get("type")
    if isinstance(stype, dict):
        stype = stype.get("value", "")
    return f"{stype or ''} {src.get('id', '')} {src.get('name', '')}".lower()


class MozartPlayer(PlayerBase):
    """B&O Mozart player via the local REST API."""

    id = "mozart"
    name = "Bang & Olufsen"
    port = 8766

    def __init__(self):
        super().__init__()
        self.ip = effective_player_ip()   # active target (override or configured)
        self._current_track_id = None
        self._current_playback_state = None
        self._peers = {}                  # ip -> {name, jid}
        self._speakers_registered = False
        self._is_follower = False         # we've joined someone's experience
        self._leader_name = ""
        self._last_state: dict = {}       # last full PlaybackState (poll or ws seed)
        self._ws_connected = False

    # ── Mozart REST helpers ──

    def _base(self, ip=None):
        return f"http://{ip or self.ip}:{MOZART_PORT}/api/v1"

    async def _get(self, path, ip=None, timeout=6):
        if self._http_session is None or self._http_session.closed:
            return None
        try:
            async with self._http_session.get(
                f"{self._base(ip)}{path}",
                timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except Exception:
            return None

    async def _cmd(self, method, path, body=None, timeout=6):
        if self._http_session is None or self._http_session.closed:
            return False
        try:
            async with self._http_session.request(
                method, f"{self._base()}{path}", json=body,
                timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                resp.raise_for_status()
                return True
        except Exception as e:
            logger.warning("Mozart %s %s failed: %s", method, path, e or type(e).__name__)
            return False

    # ── PlayerBase abstract methods ──

    async def play(self, uri=None, url=None, track_uri=None, meta=None,
                   radio=False, track_uris=None) -> bool:
        if uri:
            logger.warning("Mozart does not consume ShareLink URIs — ignoring uri=%s", uri)
        if url:
            # Uri schema: {"location": "<url>"}
            ok = await self._cmd("POST", "/playback/uri", body={"location": url})
            logger.info("Playing URL: %s", url)
            return ok
        return await self.resume()

    async def pause(self) -> bool:
        return await self._cmd("POST", "/playback/command/pause")

    async def resume(self) -> bool:
        return await self._cmd("POST", "/playback/command/play")

    async def next_track(self) -> bool:
        # PlaybackCommand enum is play|pause|stop|skip|prev — no "skipToNext".
        return await self._cmd("POST", "/playback/command/skip")

    async def prev_track(self) -> bool:
        return await self._cmd("POST", "/playback/command/prev")

    async def stop(self) -> bool:
        return await self._cmd("POST", "/playback/command/stop")

    async def get_capabilities(self) -> list:
        return ["url_stream"]

    async def get_track_uri(self) -> str:
        return self._current_track_id or ""

    async def get_status(self) -> dict:
        base = await super().get_status()
        cached = self._cached_media_data or {}
        listeners = await self._get("/beolink/listeners")
        leader = bool(listeners)
        base.update({
            "speaker_ip": self.ip,
            # Grouped either as leader (others listen to us) or follower (we
            # joined someone's experience — detected from the playback source
            # in the monitor loop). Drives the UNJOIN row / LEFT binding.
            "is_grouped": leader or self._is_follower,
            "coordinator_name": self.name if leader else self._leader_name,
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

    # ── SPEAKERS grouping capability (Beolink backend) ──

    def supports_grouping(self) -> bool:
        return True

    def group_peers_known(self) -> bool:
        return len(self._peers) > 0

    def group_resolve_name(self, name: str):
        for ip, info in self._peers.items():
            if info.get("name") == name:
                return ip
        return None

    async def _refresh_peers(self) -> dict:
        peers = {}
        data = await self._get("/beolink/peers") or []
        for p in data if isinstance(data, list) else []:
            # BeolinkPeer: friendlyName, ipAddress (a string; may be IPv6), jid
            ip = p.get("ipAddress") or p.get("ip_address")
            if ip:
                peers[ip] = {"name": p.get("friendlyName")
                             or p.get("friendly_name") or ip,
                             "jid": p.get("jid")}
        return peers

    async def _peer_now_playing(self, ip: str) -> dict:
        state = await self._get("/playback/state", ip=ip) or {}
        fields = metadata_fields(state.get("metadata"))
        return {
            "state": "playing" if render_state(state.get("state")) == "playing" else "stopped",
            "title": fields["title"],
            "artist": fields["artist"],
            "album": fields["album"],
            "artwork_url": fields["art_url"],
        }

    async def group_list_devices(self) -> list:
        self._peers = await self._refresh_peers()
        cur_ip = effective_player_ip()
        items = dict(self._peers)
        if cur_ip and cur_ip not in items:
            items[cur_ip] = {"name": self.name, "jid": None}
        ips = list(items)
        meta_by_ip = await self.gather_capped(ips, self._peer_now_playing, DEVICE_TIMEOUT)
        current, others = None, []
        for ip in ips:
            meta = meta_by_ip.get(ip) or {}
            dev = {
                "name": items[ip]["name"], "ip": ip,
                "state": meta.get("state", "stopped"),
                "title": meta.get("title", ""), "artist": meta.get("artist", ""),
                "album": meta.get("album", ""),
                "artwork_url": meta.get("artwork_url", ""),
                "group": [], "current": ip == cur_ip,
            }
            if ip == cur_ip:
                current = dev
            else:
                others.append(dev)
        others.sort(key=lambda d: d["name"])
        return ([current] if current else []) + others

    async def group_join(self, ip: str, media: dict) -> str:
        """Group with the target. When we're playing, expand our experience
        into that room (it follows us); when idle, join its experience.
        (POST /beolink/expand/{jid} and /beolink/join/{jid} per the OpenAPI;
        unvalidated on hardware.)"""
        info = self._peers.get(ip) or {}
        jid = info.get("jid")
        if not jid:
            self._peers = await self._refresh_peers()
            jid = (self._peers.get(ip) or {}).get("jid")
        if not jid:
            raise RuntimeError(f"no Beolink jid for {ip}")
        we_lead = self._current_playback_state == "playing"
        path = f"/beolink/expand/{jid}" if we_lead else f"/beolink/join/{jid}"
        if not await self._cmd("POST", path):
            raise RuntimeError("Beolink group command failed")
        return self.name if we_lead else info.get("name", ip)

    async def group_unjoin(self) -> None:
        await self._cmd("POST", "/beolink/leave")

    async def on_target_changed(self, ip: str, name: str) -> None:
        self.ip = ip
        self._current_track_id = None
        logger.info("Retargeted Mozart to %s (%s)", name or "?", ip)

    async def _register_speakers_when_found(self):
        for _ in range(10):
            self._peers = await self._refresh_peers()
            if self._peers and not self._speakers_registered:
                if await self.register_speakers_source("available"):
                    self._speakers_registered = True
                return
            await asyncio.sleep(15)

    # ── PlayerBase hooks ──

    async def on_start(self):
        if not MOZART_IP:
            logger.error("No Mozart IP configured (set player.ip in config) — exiting")
            from lib.watchdog import sd_notify
            sd_notify("READY=1\nSTATUS=No player.ip configured, exiting")
            sd_notify("STOPPING=1")
            sys.exit(0)
        logger.info("Starting Mozart player for %s", self.ip)
        self._monitor_task = self._spawn(self._monitor_loop(), name="mozart_monitor")
        self._spawn(self._ws_loop(), name="mozart_events")
        self._spawn(self._register_speakers_when_found(), name="speakers_register")

    # ── Monitoring ──
    # The event stream on :9339 is the primary channel; /playback/state is
    # polled every 3 s while the stream is down and every 30 s as a safety
    # net while it is up (the product's own app does the same seed + events).

    async def _monitor_loop(self):
        consecutive_failures = 0
        while self.running:
            try:
                state = await self._get("/playback/state")
                if state is None:
                    consecutive_failures += 1
                    if consecutive_failures == 1:
                        logger.error("Mozart unreachable (/playback/state failed)")
                    elif consecutive_failures % 40 == 0:
                        logger.warning("Mozart still unreachable (%d polls)",
                                       consecutive_failures)
                    await asyncio.sleep(min(2 ** min(consecutive_failures, 4), 30.0))
                    continue
                if consecutive_failures:
                    logger.info("Mozart reachable again after %d failed polls",
                                consecutive_failures)
                    consecutive_failures = 0
                await self._handle_state(state)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in Mozart monitoring: %s", e)
            await asyncio.sleep(POLL_INTERVAL_WS if self._ws_connected else POLL_INTERVAL)

    # ── Event stream (WebSocket) ──

    async def _ws_loop(self):
        """Keep a connection to the product's event WebSocket; seed from
        /playback/state on every (re)connect so events apply to a known
        state. Event frames are {"eventType": "WebSocketEvent…",
        "eventData": {...}} (Mozart OpenAPI WebSocketEvent* schemas)."""
        backoff = 1.0
        while self.running:
            try:
                async with self._http_session.ws_connect(
                        f"ws://{self.ip}:{MOZART_WS_PORT}/", heartbeat=30) as ws:
                    backoff = 1.0
                    self._ws_connected = True
                    logger.info("Mozart event stream connected")
                    state = await self._get("/playback/state")
                    if state:
                        await self._handle_state(state)
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                evt = json.loads(msg.data)
                            except ValueError:
                                continue
                            await self._handle_ws_event(evt)
                        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self._ws_connected or backoff == 1.0:
                    logger.warning("Mozart event stream down (%s) — polling", e or type(e).__name__)
            finally:
                if self._ws_connected:
                    logger.info("Mozart event stream disconnected")
                self._ws_connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _handle_ws_event(self, evt: dict):
        et = str(evt.get("eventType", ""))
        data = evt.get("eventData")
        if not isinstance(data, dict):
            return
        if et == "WebSocketEventVolume":
            self._spawn(self.report_volume_to_router(volume_level_from_state(data)),
                        name="report_volume")
            return
        key = {"WebSocketEventPlaybackMetadata": "metadata",
               "WebSocketEventPlaybackProgress": "progress",
               "WebSocketEventPlaybackState": "state",
               "WebSocketEventSourceChange": "source"}.get(et)
        if key is None:
            return
        state = dict(self._last_state)
        state[key] = data
        await self._handle_state(state)

    async def _handle_state(self, state: dict):
        self._last_state = state
        st = render_state(state.get("state"))
        prev = self._current_playback_state

        # Follower detection: when we've joined another room's experience the
        # active source reports as a Beolink/network-link source. Defensive —
        # the exact source shape is unvalidated on hardware.
        src = state.get("source") or {}
        blob = source_blob(src)
        self._is_follower = any(k in blob for k in ("beolink", "networklink", "joined"))
        if self._is_follower:
            self._leader_name = src.get("name") or self._leader_name
        else:
            self._leader_name = ""

        if st == "playing" and prev in ("paused", "stopped", None):
            self._spawn(self.trigger_wake(), name="trigger_wake")
            if self.seconds_since_command() > USER_ACTION_HORIZON:
                self._spawn(self.notify_router_playback_override(force=True),
                            name="playback_override")
        elif st == "stopped" and prev == "playing":
            if self.seconds_since_command() > USER_ACTION_HORIZON:
                self._spawn(self.notify_router_playback_override(force=True),
                            name="playback_override")
        self._current_playback_state = st

        fields = metadata_fields(state.get("metadata"))
        title, artist, album = fields["title"], fields["artist"], fields["album"]
        track_id = f"{title}|{artist}|{album}"

        if track_id != self._current_track_id:
            self._current_track_id = track_id
            art_url = fields["art_url"]
            artwork_b64 = None
            artwork_size = None
            if art_url:
                result = await self.fetch_artwork(art_url, session=self._http_session)
                if result:
                    artwork_b64 = result["base64"]
                    artwork_size = result["size"]
            progress = state.get("progress") or {}
            media_data = {
                "title": title or "—",
                "artist": artist or "—",
                "album": album or "—",
                "artwork": f"data:image/jpeg;base64,{artwork_b64}" if artwork_b64 else None,
                "artwork_size": artwork_size,
                "position": self._sec_to_time(progress.get("progress")),
                "duration": self._sec_to_time(duration_seconds(state)),
                "state": st,
                "speaker_ip": self.ip,
                "uri": "",
                "timestamp": int(time.time()),
            }
            self._cached_media_data = media_data
            await self.broadcast_media_update(media_data, "track_change")
            logger.info("Track changed: %s — %s", artist, title)
            if self.seconds_since_command() > USER_ACTION_HORIZON:
                self._spawn(self.notify_router_playback_override(force=True),
                            name="playback_override")
        elif self._cached_media_data:
            if self._cached_media_data.get("state") != st:
                self._cached_media_data["state"] = st
                await self.broadcast_media_update(self._cached_media_data, "state_change")

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
    player = MozartPlayer()
    await player.run()


if __name__ == "__main__":
    asyncio.run(main())
