#!/usr/bin/env python3
"""
BeoSound 5c BlueSound Player (beo-player-bluesound)

Monitors a BlueSound speaker for track changes, fetches artwork, and broadcasts
updates to the UI via WebSocket (port 8766). Also reports volume changes to
the router so the volume arc stays in sync.

BluOS HTTP API (port 11000, XML responses):
  GET /Status?etag=X&timeout=T  — long-poll for state changes
  GET /Playlist                  — queue items with artwork URLs
  GET /Play  /Pause  /Skip  /Back  /Stop  — transport controls (all GET)
  GET /Volume?level=N            — set volume (0-100)
"""

import asyncio
import logging
import os
import sys
import time
import urllib.parse
from xml.etree import ElementTree

import aiohttp

# Ensure services/ is on the path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.active_target import effective_player_ip
from lib.config import cfg
from lib.player_base import PlayerBase
from lib.timings import USER_ACTION_HORIZON

# Configuration
BLUESOUND_IP = cfg("player", "ip", default="")
BLUOS_PORT = 11000
LONG_POLL_TIMEOUT = 60   # seconds — BluOS blocks until state changes or timeout
PREFETCH_COUNT = 5

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('beo-player-bluesound')


class BluesoundPlayer(PlayerBase):
    """BlueSound player service using the BluOS HTTP/XML API."""

    id = "bluesound"
    name = "BlueSound"
    port = 8766

    def __init__(self):
        super().__init__()
        # Active target (override or configured) so a restart after "Play here"
        # keeps driving the retargeted room; BLUESOUND_IP is only the guard.
        self.ip = effective_player_ip()
        self.base_url = f"http://{self.ip}:{BLUOS_PORT}"
        # BluOS-specific state
        self._etag = ""
        self._current_track_id = None
        self._peers = {}                  # ip -> name (mDNS-discovered BluOS)
        self._speakers_registered = False

    # ── BluOS HTTP helpers ──

    async def _bluos_get(self, path: str, timeout: float = 10,
                         log_errors: bool = True) -> ElementTree.Element | None:
        """GET a BluOS endpoint, parse XML response. Returns root Element or None.

        ``log_errors=False`` demotes the failure log to DEBUG — used by the
        monitor loop, which does its own first-error/summary logging so an
        unreachable speaker doesn't flood the journal.
        """
        if self._http_session is None or self._http_session.closed:
            return None
        url = f"{self.base_url}{path}"
        try:
            async with self._http_session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                resp.raise_for_status()
                text = await resp.text()
                return ElementTree.fromstring(text)
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            if log_errors:
                logger.warning("BluOS request failed (%s): %s", path, e)
            else:
                logger.debug("BluOS request failed (%s): %s", path, e)
            return None

    def _xml_text(self, root: ElementTree.Element, tag: str, default: str = "") -> str:
        """Get text content of a child element."""
        el = root.find(tag)
        return el.text if el is not None and el.text else default

    # ── PlayerBase abstract methods ──

    async def play(self, uri=None, url=None, track_uri=None, meta=None,
                   radio=False, track_uris=None) -> bool:
        try:
            if uri:
                logger.warning("BluOS does not support Spotify URIs — ignoring uri=%s", uri)
            if url:
                encoded_url = urllib.parse.quote(url, safe="/:?&=")
                params = f"/Play?url={encoded_url}"
                if meta:
                    if meta.get('title'):
                        params += f"&title={urllib.parse.quote(meta['title'])}"
                    if meta.get('artwork_url'):
                        params += f"&image={urllib.parse.quote(meta['artwork_url'])}"
                root = await self._bluos_get(params)
                logger.info("Playing URL: %s", url)
                return root is not None
            # Resume playback
            return await self.resume()
        except Exception as e:
            logger.error("Play failed: %s", e)
            return False

    async def pause(self) -> bool:
        try:
            root = await self._bluos_get("/Pause")
            logger.info("Paused")
            return root is not None
        except Exception as e:
            logger.error("Pause failed: %s", e)
            return False

    async def resume(self) -> bool:
        try:
            root = await self._bluos_get("/Play")
            logger.info("Resumed")
            return root is not None
        except Exception as e:
            logger.error("Resume failed: %s", e)
            return False

    async def next_track(self) -> bool:
        try:
            root = await self._bluos_get("/Skip")
            logger.info("Next track")
            return root is not None
        except Exception as e:
            logger.error("Next track failed: %s", e)
            return False

    async def prev_track(self) -> bool:
        try:
            root = await self._bluos_get("/Back")
            logger.info("Previous track")
            return root is not None
        except Exception as e:
            logger.error("Previous track failed: %s", e)
            return False

    async def stop(self) -> bool:
        try:
            root = await self._bluos_get("/Stop")
            logger.info("Stopped")
            return root is not None
        except Exception as e:
            logger.error("Stop failed: %s", e)
            return False

    async def get_capabilities(self) -> list:
        return ["url_stream"]

    async def get_track_uri(self) -> str:
        return self._current_track_id or ""

    async def get_status(self) -> dict:
        base = await super().get_status()
        cached = self._cached_media_data or {}
        master_ip, slaves = await self._our_sync_status()
        base.update({
            "speaker_ip": self.ip,
            # Drives the UNJOIN row / LEFT binding in the SPEAKERS view.
            "is_grouped": bool(master_ip or slaves),
            "coordinator_name": (self._peers.get(master_ip, "") if master_ip
                                 else (self.name if slaves else "")),
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

    # ── SPEAKERS grouping capability (BluOS backend) ──
    # Discovery: mDNS _musc._tcp via avahi-browse (same source input.py uses).
    # Grouping: BluOS /AddSlave & /RemoveSlave (HTTP GET, XML). "us" in a group
    # is the controlled BlueSound at effective_player_ip(); AddSlave is issued
    # on the primary with the secondary's IP.

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
        """Return {ip: name} of BluOS players on the LAN via mDNS (_musc._tcp)."""
        peers = {}
        try:
            proc = await asyncio.create_subprocess_exec(
                'avahi-browse', '-r', '-t', '-p', '_musc._tcp',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            for line in stdout.decode(errors='replace').splitlines():
                parts = line.split(';')
                # =;<iface>;IPv4;<name>;<type>;<domain>;<host>;<addr>;<port>;<txt>
                if len(parts) < 9 or parts[0] != '=' or parts[2] != 'IPv4':
                    continue
                name, addr = parts[3], parts[7]
                if addr:
                    peers[addr] = name
        except (asyncio.TimeoutError, FileNotFoundError):
            pass
        except Exception as e:
            logger.debug("BluOS peer discovery failed: %s", e)
        return peers

    async def _peer_status(self, ip: str) -> dict:
        """GET a peer's /Status for now-playing (best-effort)."""
        try:
            async with self._http_session.get(
                f"http://{ip}:{BLUOS_PORT}/Status",
                timeout=aiohttp.ClientTimeout(total=4)) as resp:
                resp.raise_for_status()
                root = ElementTree.fromstring(await resp.text())
        except Exception:
            return {}
        raw = self._xml_text(root, "state", "stop")
        image = self._xml_text(root, "image")
        if image.startswith("/"):
            image = f"http://{ip}:{BLUOS_PORT}{image}"
        return {
            "state": "playing" if raw in ("play", "stream") else "stopped",
            "title": self._xml_text(root, "name") or self._xml_text(root, "title1"),
            "artist": self._xml_text(root, "artist") or self._xml_text(root, "title2"),
            "album": self._xml_text(root, "album") or self._xml_text(root, "title3"),
            "artwork_url": image,
        }

    async def group_list_devices(self) -> list:
        peers = await self._discover_peers()
        self._peers = peers
        cur_ip = effective_player_ip()
        items = dict(peers)
        if cur_ip and cur_ip not in items:
            items[cur_ip] = self.name  # ensure the current target is listed
        ips = list(items)
        # Per peer, in parallel: /Status (now-playing) + /SyncStatus (group).
        statuses, syncs = await asyncio.gather(
            asyncio.gather(*[self._peer_status(ip) for ip in ips],
                           return_exceptions=True),
            asyncio.gather(*[self._peer_slaves(ip) for ip in ips],
                           return_exceptions=True),
        )
        current, others = None, []
        for ip, st, slaves in zip(ips, statuses, syncs):
            st = st if isinstance(st, dict) else {}
            slaves = slaves if isinstance(slaves, list) else []
            dev = {
                "name": items[ip], "ip": ip,
                "state": st.get("state", "stopped"),
                "title": st.get("title", ""), "artist": st.get("artist", ""),
                "album": st.get("album", ""), "artwork_url": st.get("artwork_url", ""),
                "group": [items.get(s, s) for s in slaves],
                "current": ip == cur_ip,
            }
            if ip == cur_ip:
                current = dev
            else:
                others.append(dev)
        others.sort(key=lambda d: d["name"])
        return ([current] if current else []) + others

    async def _peer_slaves(self, ip: str) -> list:
        """Return the secondary IPs grouped under ``ip`` (empty if not a primary)."""
        try:
            async with self._http_session.get(
                f"http://{ip}:{BLUOS_PORT}/SyncStatus",
                timeout=aiohttp.ClientTimeout(total=4)) as resp:
                resp.raise_for_status()
                root = ElementTree.fromstring(await resp.text())
        except Exception:
            return []
        return [s.get("id") for s in root.findall("slave") if s.get("id")]

    async def _our_sync_status(self):
        """Parse our /SyncStatus → (master_ip or None, [slave_ips])."""
        try:
            async with self._http_session.get(
                f"http://{effective_player_ip()}:{BLUOS_PORT}/SyncStatus",
                timeout=aiohttp.ClientTimeout(total=4)) as resp:
                resp.raise_for_status()
                root = ElementTree.fromstring(await resp.text())
        except Exception:
            return None, []
        master = root.find("master")
        master_ip = master.text if master is not None and master.text else None
        slaves = [s.get("id") for s in root.findall("slave") if s.get("id")]
        return master_ip, slaves

    async def group_join(self, ip: str, media: dict) -> str:
        """Group with the target. If we're playing we're the primary (target
        joins us); otherwise we join the target's group as a secondary."""
        our_ip = effective_player_ip()
        we_lead = self._current_playback_state == "playing"
        primary, secondary = (our_ip, ip) if we_lead else (ip, our_ip)
        async with self._http_session.get(
            f"http://{primary}:{BLUOS_PORT}/AddSlave"
            f"?slave={secondary}&port={BLUOS_PORT}",
            timeout=aiohttp.ClientTimeout(total=8)) as resp:
            resp.raise_for_status()
        return self._peers.get(primary, primary)

    async def group_unjoin(self) -> None:
        our_ip = effective_player_ip()
        master_ip, slaves = await self._our_sync_status()
        if master_ip:
            async with self._http_session.get(
                f"http://{master_ip}:{BLUOS_PORT}/RemoveSlave"
                f"?slave={our_ip}&port={BLUOS_PORT}",
                timeout=aiohttp.ClientTimeout(total=8)) as resp:
                resp.raise_for_status()
        elif slaves:
            # We're the primary — drop every secondary to disband the group.
            for s in slaves:
                try:
                    async with self._http_session.get(
                        f"http://{our_ip}:{BLUOS_PORT}/RemoveSlave"
                        f"?slave={s}&port={BLUOS_PORT}",
                        timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        resp.raise_for_status()
                except Exception as e:
                    logger.debug("RemoveSlave %s failed: %s", s, e)

    async def on_target_changed(self, ip: str, name: str) -> None:
        self.ip = ip
        self.base_url = f"http://{ip}:{BLUOS_PORT}"
        self._etag = ""  # force a fresh long-poll against the new device
        logger.info("Retargeted BlueSound to %s (%s)", name or "?", ip)

    async def _register_speakers_when_found(self):
        """Advertise the SPEAKERS menu entry once other BluOS rooms appear."""
        for _ in range(10):
            self._peers = await self._discover_peers()
            if len(self._peers) > 1 and not self._speakers_registered:
                if await self.register_speakers_source("available"):
                    self._speakers_registered = True
                return
            await asyncio.sleep(15)

    # ── PlayerBase hooks ──

    async def on_start(self):
        if not BLUESOUND_IP:
            # Exit 0, not 1 — with Restart=on-failure this keeps the
            # misconfigured service stopped instead of crash-looping
            # (same convention as the player-type guard in
            # PlayerBase.start()).
            logger.error("No BlueSound IP configured (set player.ip in "
                         "config) — exiting")
            # Tell systemd we started and are stopping. READY=1 must be
            # sent before exiting: the unit is Type=notify and the normal
            # READY=1 comes from watchdog_loop, which never runs on this
            # path — exiting without it makes systemd record
            # Result=protocol (a failure) and Restart=on-failure
            # crash-loops the service despite the exit code 0.
            from lib.watchdog import sd_notify
            sd_notify("READY=1\nSTATUS=No player.ip configured, exiting")
            sd_notify("STOPPING=1")
            sys.exit(0)
        logger.info("Starting BlueSound player for %s", self.base_url)
        self._monitor_task = self._spawn(self._monitor_bluos(), name="bluos_monitor")
        self._spawn(self._register_speakers_when_found(), name="speakers_register")

    # ── Monitoring (long-poll loop) ──

    async def _monitor_bluos(self):
        """Long-poll /Status for state changes."""
        logger.info("Starting BluOS monitoring for %s", self.base_url)
        consecutive_failures = 0

        while self.running:
            try:
                # Build long-poll URL
                path = f"/Status?timeout={LONG_POLL_TIMEOUT}"
                if self._etag:
                    path += f"&etag={self._etag}"

                root = await self._bluos_get(
                    path, timeout=LONG_POLL_TIMEOUT + 10, log_errors=False)

                if root is None:
                    # Back off while the speaker is unreachable. With a
                    # wrong/offline player.ip every cycle fails — at a 1s
                    # retry that's a warning per second into the journal
                    # forever. Log the first failure, back off to 30s,
                    # emit a periodic summary, and resume the fast poll
                    # as soon as a cycle succeeds (mirrors sonos.py).
                    consecutive_failures += 1
                    if consecutive_failures == 1:
                        logger.error("Error in BluOS monitoring: /Status "
                                     "poll failed (speaker unreachable?)")
                    elif consecutive_failures % 120 == 0:
                        logger.warning("BluOS still unreachable (%d "
                                       "consecutive failed polls)",
                                       consecutive_failures)
                    await asyncio.sleep(
                        min(2 ** min(consecutive_failures, 5), 30.0))
                    continue

                if consecutive_failures:
                    logger.info("BluOS reachable again after %d failed polls",
                                consecutive_failures)
                    consecutive_failures = 0

                # Update etag for next long-poll
                self._etag = root.get("etag", self._etag)

                raw_state = self._xml_text(root, "state", "stop")
                if raw_state in ("play", "stream"):
                    state = "playing"
                elif raw_state == "pause":
                    state = "paused"
                else:
                    state = "stopped"

                prev_state = self._current_playback_state

                # Wake trigger on transition to playing
                if state == "playing" and self._current_playback_state in (
                    "paused", "stopped", None
                ):
                    logger.info("Playback started (was: %s), triggering wake",
                                self._current_playback_state)
                    self._spawn(self.trigger_wake(), name="trigger_wake")
                    if self.seconds_since_command() > USER_ACTION_HORIZON:
                        logger.info("External playback detected, clearing active source")
                        self._spawn(
                            self.notify_router_playback_override(force=True),
                            name="playback_override")
                elif state == "stopped" and self._current_playback_state == "playing":
                    if self.seconds_since_command() > USER_ACTION_HORIZON:
                        logger.info("External stop detected")
                        self._spawn(
                            self.notify_router_playback_override(force=True),
                            name="playback_override")

                self._current_playback_state = state

                # Broadcast on play<->pause/stop transitions with the same
                # track — the track didn't change, so the track_change
                # broadcast below won't fire (mirrors sonos.py). Both
                # directions matter: pause/stop so the UI leaves the
                # "playing" state, and resume so an externally-paused UI
                # doesn't stay stuck on "paused" until the next track.
                state_transition = (
                    (prev_state == "playing" and state in ("paused", "stopped"))
                    or (prev_state in ("paused", "stopped") and state == "playing")
                )
                if state_transition and self._cached_media_data:
                    state_data = dict(self._cached_media_data)
                    state_data["state"] = state
                    await self.broadcast_media_update(state_data, "state_change")

                # Volume reporting (base method deduplicates)
                vol_str = self._xml_text(root, "volume")
                if vol_str:
                    try:
                        self._spawn(
                            self.report_volume_to_router(int(vol_str)),
                            name="report_volume")
                    except ValueError:
                        pass

                # Track change detection
                title = self._xml_text(root, "name") or self._xml_text(root, "title1")
                artist = self._xml_text(root, "artist") or self._xml_text(root, "title2")
                album = self._xml_text(root, "album") or self._xml_text(root, "title3")
                image_url = self._xml_text(root, "image")
                song_index = self._xml_text(root, "song")
                service = self._xml_text(root, "service")
                quality = self._xml_text(root, "quality")
                secs = self._xml_text(root, "secs")
                totlen = self._xml_text(root, "totlen")

                track_id = f"{title}|{artist}|{album}"
                track_changed = track_id != self._current_track_id

                if track_changed:
                    self._current_track_id = track_id

                    artwork_base64 = None
                    artwork_size = None
                    if image_url:
                        # Make URL absolute if relative
                        if image_url.startswith("/"):
                            image_url = f"{self.base_url}{image_url}"
                        result = await self.fetch_artwork(image_url, session=self._http_session)
                        if result:
                            artwork_base64 = result["base64"]
                            artwork_size = result["size"]

                    # Format position/duration
                    position = self._seconds_to_time(secs)
                    duration = self._seconds_to_time(totlen)

                    media_data = {
                        "title": title or "—",
                        "artist": artist or "—",
                        "album": album or "—",
                        "artwork": f"data:image/jpeg;base64,{artwork_base64}" if artwork_base64 else None,
                        "artwork_size": artwork_size,
                        "position": position,
                        "duration": duration,
                        "state": state,
                        "volume": int(vol_str) if vol_str else 0,
                        "speaker_ip": self.ip,
                        "service": service,
                        "quality": quality,
                        "uri": f"bluos:{song_index}" if song_index else "",
                        "timestamp": int(time.time()),
                    }

                    self._cached_media_data = media_data

                    await self.broadcast_media_update(media_data, "track_change")
                    logger.info("Track changed: %s — %s", artist, title)

                    # Prefetch upcoming artwork
                    self._spawn(self._prefetch_queue_artwork(), name="prefetch_artwork")

                    # External track change? Clear active source
                    if self.seconds_since_command() > USER_ACTION_HORIZON:
                        self._spawn(
                            self.notify_router_playback_override(force=True),
                            name="playback_override")

                elif self._cached_media_data:
                    # Update position/state in cached data; broadcast when the
                    # state itself changed (e.g. external pause from the BluOS
                    # app) — otherwise the UI shows "playing" indefinitely
                    state_changed = self._cached_media_data.get("state") != state
                    self._cached_media_data["state"] = state
                    self._cached_media_data["position"] = self._seconds_to_time(secs)
                    if vol_str:
                        self._cached_media_data["volume"] = int(vol_str)
                    if state_changed:
                        logger.info("Playback state changed → %s", state)
                        await self.broadcast_media_update(
                            self._cached_media_data, "state_change")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in BluOS monitoring: %s", e)
                await asyncio.sleep(2)

    # ── Queue prefetch ──

    async def _prefetch_queue_artwork(self):
        """Prefetch artwork for upcoming tracks from /Playlist."""
        try:
            root = await self._bluos_get("/Playlist")
            if root is None:
                return

            songs = root.findall(".//song")
            current_index = -1
            # Find current song index
            for i, song in enumerate(songs):
                if song.get("selected") == "selected":
                    current_index = i
                    break

            # Prefetch next N tracks
            start = max(current_index + 1, 0)
            tasks = []
            for song in songs[start:start + PREFETCH_COUNT]:
                img = song.get("image") or self._xml_text(song, "image")
                if img:
                    if img.startswith("/"):
                        img = f"{self.base_url}{img}"
                    if img not in self._artwork_cache:
                        tasks.append(self.fetch_artwork(img, session=self._http_session))

            if tasks:
                logger.info("Prefetching artwork for %d upcoming tracks", len(tasks))
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=15.0,
                )
        except asyncio.TimeoutError:
            logger.warning("Prefetch timed out")
        except Exception as e:
            logger.debug("Queue prefetch error: %s", e)

    # ── Helpers ──

    @staticmethod
    def _seconds_to_time(secs_str: str) -> str:
        """Convert seconds string to MM:SS or HH:MM:SS."""
        try:
            total = int(float(secs_str))
        except (ValueError, TypeError):
            return "0:00"
        if total >= 3600:
            h = total // 3600
            m = (total % 3600) // 60
            s = total % 60
            return f"{h}:{m:02d}:{s:02d}"
        return f"{total // 60}:{total % 60:02d}"

async def main():
    player = BluesoundPlayer()
    await player.run()


if __name__ == "__main__":
    asyncio.run(main())
