#!/usr/bin/env python3
"""
BeoSound 5c B&O Mozart Player (beo-player-mozart) — EXPERIMENTAL

Drives a modern Bang & Olufsen speaker on the Mozart platform (Beolab 8/28,
Beosound A5/A9-5thgen/Balance/Emerge/Level, Beoconnect Core, Premiere/Theatre,
"any Mozart-based product post-2020") via B&O's official local REST API.

Mozart Open API: https://github.com/bang-olufsen/mozart-open-api
  REST base: http://<ip>:8080/api/v1   (no auth on the local network)
  GET  /playback/state                 — state + metadata (title/artist/art)
  POST /playback/command/{command}     — play|pause|stop|skipToNext|skipToPrevious
  POST /playback/uri                   — play an arbitrary URL {"uri": ...}
  GET/PUT /sound/volume/level          — volume {"level": 0-100}
Multiroom (Beolink):
  GET  /beolink/peers                  — other B&O rooms (friendlyName, ip, jid)
  POST /beolink/join/{jid}             — join a peer's experience
  POST /beolink/leave                  — leave

No SSDP/mDNS needed: /beolink/peers is the network roster. Not yet validated
against real hardware.
"""

import asyncio
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

MOZART_IP = cfg("player", "ip", default="")
MOZART_PORT = 8080
POLL_INTERVAL = 3.0
DEVICE_TIMEOUT = 8        # cap for the cold /player/network fan-out

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('beo-player-mozart')


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
            logger.warning("Mozart %s %s failed: %s", method, path, e)
            return False

    # ── PlayerBase abstract methods ──

    async def play(self, uri=None, url=None, track_uri=None, meta=None,
                   radio=False, track_uris=None) -> bool:
        if uri:
            logger.warning("Mozart does not consume ShareLink URIs — ignoring uri=%s", uri)
        if url:
            ok = await self._cmd("POST", "/playback/uri", body={"uri": url})
            logger.info("Playing URL: %s", url)
            return ok
        return await self.resume()

    async def pause(self) -> bool:
        return await self._cmd("POST", "/playback/command/pause")

    async def resume(self) -> bool:
        return await self._cmd("POST", "/playback/command/play")

    async def next_track(self) -> bool:
        return await self._cmd("POST", "/playback/command/skipToNext")

    async def prev_track(self) -> bool:
        return await self._cmd("POST", "/playback/command/skipToPrevious")

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
            ip = p.get("ipAddress") or p.get("ip_address")
            if ip:
                peers[ip] = {"name": p.get("friendlyName")
                             or p.get("friendly_name") or ip,
                             "jid": p.get("jid")}
        return peers

    async def _peer_now_playing(self, ip: str) -> dict:
        state = await self._get("/playback/state", ip=ip) or {}
        md = state.get("metadata") or {}
        art = md.get("art") or []
        art_url = ""
        if isinstance(art, list) and art:
            art_url = art[0].get("url", "")
        raw = str(state.get("state", "")).lower()
        return {
            "state": "playing" if raw in ("play", "playing", "started") else "stopped",
            "title": md.get("title", ""),
            "artist": md.get("artist", ""),
            "album": md.get("albumTitle") or md.get("album_title", ""),
            "artwork_url": art_url,
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
        (Endpoint names per the Mozart Open API; unvalidated on hardware.)"""
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
        self._spawn(self._register_speakers_when_found(), name="speakers_register")

    # ── Monitoring (poll loop) ──

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
            await asyncio.sleep(POLL_INTERVAL)

    async def _handle_state(self, state: dict):
        raw = str(state.get("state", "")).lower()
        st = "playing" if raw in ("play", "playing", "started") else (
            "paused" if raw in ("pause", "paused") else "stopped")
        prev = self._current_playback_state

        # Follower detection: when we've joined another room's experience the
        # active source reports as a Beolink/network-link source. Defensive —
        # the exact source shape is unvalidated on hardware.
        src = state.get("source") or {}
        src_blob = f"{src.get('type', '')} {src.get('id', '')} {src.get('name', '')}".lower()
        self._is_follower = any(k in src_blob for k in ("beolink", "networklink", "joined"))
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

        md = state.get("metadata") or {}
        title = md.get("title", "") or ""
        artist = md.get("artist", "") or ""
        album = md.get("albumTitle") or md.get("album_title", "") or ""
        track_id = f"{title}|{artist}|{album}"

        if track_id != self._current_track_id:
            self._current_track_id = track_id
            art = md.get("art") or []
            art_url = art[0].get("url", "") if isinstance(art, list) and art else ""
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
                "duration": self._sec_to_time(progress.get("totalDuration")
                                              or md.get("duration")),
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
