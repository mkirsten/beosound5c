#!/usr/bin/env python3
"""
BeoSound 5c WiiM / LinkPlay Player (beo-player-wiim)

Monitors a WiiM (or other LinkPlay) speaker, fetches artwork, and broadcasts
updates to the UI via the router (port 8766). Also reports volume changes so
the volume arc stays in sync, and implements SPEAKERS grouping via LinkPlay
multiroom.

LinkPlay HTTP API (httpapi.asp). Newer WiiM firmware serves HTTPS with a
self-signed cert (so certificate verification is disabled); older LinkPlay
devices serve plain HTTP — we try HTTPS first and fall back. There is no
long-poll, so state is polled.

Commands used:
  getStatusEx                       — device info (ssid/name, uuid, group, IP)
  getPlayerStatus                   — status, vol, mute, curpos, totlen
  getMetaInfo                       — title/artist/album/albumArtURI
  setPlayerCmd:play:<url>           — play a URL
  setPlayerCmd:{pause,resume,onepause,prev,next,stop}
  setPlayerCmd:vol:<0-100> / :mute:<0|1>
Grouping (multiroom):
  ConnectMasterAp:JoinGroupMaster:eth<masterIP>   — slave-initiated join
  multiroom:Ungroup                 — leave/disband
  multiroom:SlaveKickout:<slaveIP>  — drop a secondary
  multiroom:getSlaveList            — a master's current slaves
"""

import asyncio
import json
import logging
import os
import socket
import sys
import time

import aiohttp

# Ensure services/ is on the path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.active_target import effective_player_ip
from lib.config import cfg
from lib.player_base import PlayerBase
from lib.timings import USER_ACTION_HORIZON

WIIM_IP = cfg("player", "ip", default="")
POLL_INTERVAL = 2.0       # LinkPlay has no long-poll — poll state
DISCOVERY_REGISTER_TRIES = 10
DEVICE_TIMEOUT = 8        # cap for the cold /player/network fan-out
GROUP_CHECK_INTERVAL = 8  # how often the monitor recomputes grouping state
SCHEME_RELEARN_AFTER = 3  # reset the http/https latch after N failed polls

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('beo-player-wiim')


class WiimPlayer(PlayerBase):
    """WiiM / LinkPlay player service using the LinkPlay HTTP API."""

    id = "wiim"
    name = "WiiM"
    port = 8766

    def __init__(self):
        super().__init__()
        # Active target (override or configured) so a restart after "Play here"
        # keeps driving the retargeted room; WIIM_IP is only the guard.
        self.ip = effective_player_ip()
        self._scheme = None            # 'https' or 'http' — learned on first call
        self._current_track_id = None
        self._current_playback_state = None
        self._peers = {}               # ip -> name (SSDP-discovered LinkPlay)
        self._speakers_registered = False
        self._grouped_cached = False   # computed in the monitor loop (§9)
        self._last_group_check = 0.0

    # ── LinkPlay HTTP helpers ──

    async def _lp_get(self, ip: str, command: str, want_json: bool = False,
                      timeout: float = 6):
        """GET httpapi.asp?command=<command> against ``ip``.

        Tries HTTPS (self-signed → no verification) then HTTP, remembering
        which scheme answered so later calls skip the dead one."""
        schemes = ([self._scheme] if ip == self.ip and self._scheme
                   else ["https", "http"])
        for scheme in schemes:
            url = f"{scheme}://{ip}/httpapi.asp?command={command}"
            try:
                async with self._http_session.get(
                    url, ssl=False,
                    timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    resp.raise_for_status()
                    text = await resp.text()
            except Exception:
                continue
            if ip == self.ip:
                self._scheme = scheme
            if not want_json:
                return text
            try:
                return json.loads(text)
            except (ValueError, TypeError):
                return None
        return None

    async def _cmd(self, command: str) -> bool:
        """Fire a setPlayerCmd/multiroom command against our own device."""
        resp = await self._lp_get(self.ip, command)
        return resp is not None and "OK" in resp.upper()

    # ── PlayerBase abstract methods ──

    async def play(self, uri=None, url=None, track_uri=None, meta=None,
                   radio=False, track_uris=None) -> bool:
        if uri:
            logger.warning("LinkPlay does not support Spotify URIs — ignoring uri=%s", uri)
        if url:
            ok = await self._cmd(f"setPlayerCmd:play:{url}")
            logger.info("Playing URL: %s", url)
            return ok
        return await self.resume()

    async def pause(self) -> bool:
        return await self._cmd("setPlayerCmd:pause")

    async def resume(self) -> bool:
        return await self._cmd("setPlayerCmd:resume")

    async def next_track(self) -> bool:
        return await self._cmd("setPlayerCmd:next")

    async def prev_track(self) -> bool:
        return await self._cmd("setPlayerCmd:prev")

    async def stop(self) -> bool:
        return await self._cmd("setPlayerCmd:stop")

    async def get_capabilities(self) -> list:
        return ["url_stream"]

    async def get_track_uri(self) -> str:
        return self._current_track_id or ""

    async def get_status(self) -> dict:
        base = await super().get_status()
        cached = self._cached_media_data or {}
        base.update({
            "speaker_ip": self.ip,
            # Cached by the monitor loop — /player/status is polled every 5s
            # alongside /player/network, so it must not itself make blocking
            # LinkPlay calls (§9).
            "is_grouped": self._grouped_cached,
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

    # ── SPEAKERS grouping capability (LinkPlay backend) ──

    def supports_grouping(self) -> bool:
        return True

    def group_peers_known(self) -> bool:
        return len(self._peers) > 1

    def group_resolve_name(self, name: str):
        for ip, n in self._peers.items():
            if n == name:
                return ip
        return None

    async def _discover_peers(self) -> dict:
        """SSDP M-SEARCH for LinkPlay MediaRenderers → {ip: name}.

        LinkPlay announces over SSDP (UPnP), not mDNS. Collect responder IPs,
        then confirm/label each via getStatusEx (which only LinkPlay answers
        with a uuid + wmrm_version)."""
        loop = asyncio.get_running_loop()
        try:
            ips = await loop.run_in_executor(None, self._ssdp_search)
        except Exception as e:
            logger.debug("LinkPlay SSDP search failed: %s", e)
            ips = set()
        ips.add(self.ip)
        # SSDP catches every MediaRenderer (TVs, AVRs) — cap the getStatusEx
        # fan-out so non-LinkPlay devices that burn the https-then-http timeout
        # can't stall the cold /player/network call.
        statuses = await self.gather_capped(
            list(ips),
            lambda ip: self._lp_get(ip, "getStatusEx", want_json=True),
            DEVICE_TIMEOUT)
        peers = {}
        for ip, info in statuses.items():
            if isinstance(info, dict) and info.get("uuid"):
                peers[ip] = info.get("ssid") or info.get("DeviceName") or ip
        return peers

    @staticmethod
    def _ssdp_search(timeout: float = 2.0) -> set:
        """Blocking SSDP M-SEARCH — returns a set of responder IPs."""
        msg = (
            'M-SEARCH * HTTP/1.1\r\n'
            'HOST: 239.255.255.250:1900\r\n'
            'MAN: "ssdp:discover"\r\n'
            'MX: 2\r\n'
            'ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n'
            '\r\n'
        ).encode()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(timeout)
        found = set()
        try:
            s.sendto(msg, ("239.255.255.250", 1900))
            while True:
                try:
                    _, addr = s.recvfrom(2048)
                    found.add(addr[0])
                except socket.timeout:
                    break
        finally:
            s.close()
        return found

    @staticmethod
    def _album_art(md: dict) -> str:
        # Some firmware reports the key with a trailing space.
        return (md.get("albumArtURI") or md.get("albumArtURI ") or "").strip()

    async def group_list_devices(self) -> list:
        peers = await self._discover_peers()
        self._peers = peers
        cur_ip = effective_player_ip()
        items = dict(peers)
        if cur_ip and cur_ip not in items:
            items[cur_ip] = self.name
        ips = list(items)
        meta_by_ip = await self.gather_capped(ips, self._peer_now_playing, DEVICE_TIMEOUT)
        current, others = None, []
        for ip in ips:
            meta = meta_by_ip.get(ip) or {}
            dev = {
                "name": items[ip], "ip": ip,
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

    async def _peer_now_playing(self, ip: str) -> dict:
        status = await self._lp_get(ip, "getPlayerStatus", want_json=True)
        meta = await self._lp_get(ip, "getMetaInfo", want_json=True)
        raw = (status or {}).get("status", "stop")
        md = (meta or {}).get("metaData", {}) if isinstance(meta, dict) else {}
        return {
            "state": "playing" if raw == "play" else "stopped",
            "title": md.get("title", ""),
            "artist": md.get("artist", ""),
            "album": md.get("album", ""),
            "artwork_url": self._album_art(md),
        }

    async def group_join(self, ip: str, media: dict) -> str:
        """Group with the target. LinkPlay join is slave-initiated: the joining
        device points at the master's IP. If we're playing we're the master
        (target joins us); otherwise we join the target's group."""
        our_ip = effective_player_ip()
        we_lead = self._current_playback_state == "playing"
        master, joiner = (our_ip, ip) if we_lead else (ip, our_ip)
        ok = await self._lp_get(joiner, f"ConnectMasterAp:JoinGroupMaster:eth{master}")
        if ok is None:
            raise RuntimeError("LinkPlay join command failed")
        return self._peers.get(master, master)

    async def group_unjoin(self) -> None:
        # Ungroup on our own device leaves the group (or disbands if we're master).
        await self._lp_get(self.ip, "multiroom:Ungroup")

    async def _is_grouped(self) -> bool:
        """True if we're a slave (getStatusEx group=1) or a master with slaves."""
        info = await self._lp_get(self.ip, "getStatusEx", want_json=True) or {}
        if str(info.get("group", "0")) == "1":
            return True
        sl = await self._lp_get(self.ip, "multiroom:getSlaveList", want_json=True) or {}
        try:
            return int(sl.get("slaves", 0)) > 0
        except (ValueError, TypeError):
            return bool(sl.get("slave_list"))

    async def on_target_changed(self, ip: str, name: str) -> None:
        self.ip = ip
        self._scheme = None            # re-learn scheme for the new device
        self._current_track_id = None
        logger.info("Retargeted WiiM to %s (%s)", name or "?", ip)

    async def _register_speakers_when_found(self):
        for _ in range(DISCOVERY_REGISTER_TRIES):
            self._peers = await self._discover_peers()
            if len(self._peers) > 1 and not self._speakers_registered:
                if await self.register_speakers_source("available"):
                    self._speakers_registered = True
                return
            await asyncio.sleep(15)

    # ── PlayerBase hooks ──

    async def on_start(self):
        if not WIIM_IP:
            # Exit 0, not 1 — with Restart=on-failure this keeps the
            # misconfigured service stopped instead of crash-looping.
            logger.error("No WiiM IP configured (set player.ip in config) — exiting")
            from lib.watchdog import sd_notify
            sd_notify("READY=1\nSTATUS=No player.ip configured, exiting")
            sd_notify("STOPPING=1")
            sys.exit(0)
        logger.info("Starting WiiM player for %s", WIIM_IP)
        self._monitor_task = self._spawn(self._monitor_loop(), name="wiim_monitor")
        self._spawn(self._register_speakers_when_found(), name="speakers_register")

    # ── Monitoring (poll loop) ──

    async def _monitor_loop(self):
        consecutive_failures = 0
        while self.running:
            try:
                status = await self._lp_get(self.ip, "getPlayerStatus", want_json=True)
                if status is None:
                    consecutive_failures += 1
                    if consecutive_failures == 1:
                        logger.error("WiiM unreachable (getPlayerStatus failed)")
                    elif consecutive_failures % 60 == 0:
                        logger.warning("WiiM still unreachable (%d polls)",
                                       consecutive_failures)
                    # Re-probe the http/https scheme after a few failures — a
                    # firmware update or a swapped device at the same IP can
                    # change it, and the latch would otherwise wedge us (§10).
                    if consecutive_failures == SCHEME_RELEARN_AFTER:
                        self._scheme = None
                    await asyncio.sleep(min(2 ** min(consecutive_failures, 4), 30.0))
                    continue
                if consecutive_failures:
                    logger.info("WiiM reachable again after %d failed polls",
                                consecutive_failures)
                    consecutive_failures = 0

                await self._handle_status(status)

                # Recompute grouping state here (throttled) so /player/status
                # stays a cheap cache read (§9).
                now = time.monotonic()
                if now - self._last_group_check >= GROUP_CHECK_INTERVAL:
                    self._last_group_check = now
                    try:
                        self._grouped_cached = await self._is_grouped()
                    except Exception:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in WiiM monitoring: %s", e)
            await asyncio.sleep(POLL_INTERVAL)

    async def _handle_status(self, status: dict):
        raw = status.get("status", "stop")
        state = {"play": "playing", "pause": "paused", "loading": "playing"}.get(
            raw, "stopped")
        prev_state = self._current_playback_state

        if state == "playing" and prev_state in ("paused", "stopped", None):
            self._spawn(self.trigger_wake(), name="trigger_wake")
            if self.seconds_since_command() > USER_ACTION_HORIZON:
                self._spawn(self.notify_router_playback_override(force=True),
                            name="playback_override")
        elif state == "stopped" and prev_state == "playing":
            if self.seconds_since_command() > USER_ACTION_HORIZON:
                self._spawn(self.notify_router_playback_override(force=True),
                            name="playback_override")

        self._current_playback_state = state

        vol = status.get("vol")
        if vol is not None:
            try:
                self._spawn(self.report_volume_to_router(int(vol)),
                            name="report_volume")
            except (ValueError, TypeError):
                pass

        # Metadata (separate endpoint on WiiM)
        meta = await self._lp_get(self.ip, "getMetaInfo", want_json=True)
        md = (meta or {}).get("metaData", {}) if isinstance(meta, dict) else {}
        title = md.get("title", "") or ""
        artist = md.get("artist", "") or ""
        album = md.get("album", "") or ""
        track_id = f"{title}|{artist}|{album}"

        if track_id != self._current_track_id:
            self._current_track_id = track_id
            artwork_b64 = None
            artwork_size = None
            art_url = self._album_art(md)
            if art_url:
                result = await self.fetch_artwork(art_url, session=self._http_session)
                if result:
                    artwork_b64 = result["base64"]
                    artwork_size = result["size"]

            media_data = {
                "title": title or "—",
                "artist": artist or "—",
                "album": album or "—",
                "artwork": f"data:image/jpeg;base64,{artwork_b64}" if artwork_b64 else None,
                "artwork_size": artwork_size,
                "position": self._ms_to_time(status.get("curpos")),
                "duration": self._ms_to_time(status.get("totlen")),
                "state": state,
                "volume": int(vol) if vol is not None else 0,
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
            state_changed = self._cached_media_data.get("state") != state
            self._cached_media_data["state"] = state
            self._cached_media_data["position"] = self._ms_to_time(status.get("curpos"))
            if state_changed:
                await self.broadcast_media_update(self._cached_media_data, "state_change")

    # ── Helpers ──

    @staticmethod
    def _ms_to_time(ms) -> str:
        try:
            total = int(ms) // 1000
        except (ValueError, TypeError):
            return "0:00"
        if total >= 3600:
            h = total // 3600
            m = (total % 3600) // 60
            s = total % 60
            return f"{h}:{m:02d}:{s:02d}"
        return f"{total // 60}:{total % 60:02d}"


async def main():
    player = WiimPlayer()
    await player.run()


if __name__ == "__main__":
    asyncio.run(main())
