"""
go-librespot client — thin wrapper for the local HTTP + WebSocket API.

Used by the local player service to play Spotify content via go-librespot.
Handles URI conversion, playback commands, and real-time event monitoring.
"""

import asyncio
import json
import logging
import re

import aiohttp

log = logging.getLogger('beo-player-local')

LIBRESPOT_PORT = 3678
LIBRESPOT_BASE = f'http://localhost:{LIBRESPOT_PORT}'

# Share URL → Spotify URI conversion
# https://open.spotify.com/playlist/xxx → spotify:playlist:xxx
_SHARE_URL_RE = re.compile(
    r'https?://open\.spotify\.com/(?:intl-\w+/)?(\w+)/([A-Za-z0-9]+)')


def share_url_to_uri(url: str) -> str | None:
    """Convert a Spotify share URL to a native Spotify URI.
    Returns None if the URL is not a recognized Spotify share URL."""
    m = _SHARE_URL_RE.match(url)
    if m:
        return f'spotify:{m.group(1)}:{m.group(2)}'
    # Already a spotify: URI
    if url.startswith('spotify:'):
        return url
    return None


class LibrespotClient:
    """Client for go-librespot's local HTTP API + WebSocket event stream."""

    def __init__(self, on_event=None):
        self._session: aiohttp.ClientSession | None = None
        self._ws_task: asyncio.Task | None = None
        self._on_event = on_event  # async callback(event_type: str, data: dict)
        self.device_id: str | None = None
        self.connected = False

    async def start(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10))
        if self.connected:
            self._start_event_stream()

    async def stop(self):
        self._stop_event_stream()
        if self._session:
            await self._session.close()
            self._session = None
        self.connected = False

    def _start_event_stream(self):
        if not self._ws_task or self._ws_task.done():
            self._ws_task = asyncio.create_task(self._event_loop())

    def _stop_event_stream(self):
        if self._ws_task:
            self._ws_task.cancel()
            try:
                self._ws_task = None
            except Exception:
                pass

    async def check_available(self) -> bool:
        """Check if go-librespot is reachable and get its device ID."""
        session = self._session or aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=3))
        try:
            # /status returns 200 with session data, or 204 when not yet
            # authenticated (zeroconf pairing hasn't happened yet).
            # Either means the daemon is running and reachable.
            async with session.get(f'{LIBRESPOT_BASE}/status') as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.device_id = data.get('device_id')
                    self.connected = True
                    log.info("go-librespot available (device_id=%s, name=%s)",
                             self.device_id, data.get('device_name'))
                    return True
                elif resp.status == 204:
                    # Daemon running but no active Spotify session yet
                    self.connected = True
                    log.info("go-librespot available (awaiting zeroconf pairing)")
                    return True
        except Exception as e:
            log.debug("go-librespot not reachable: %s", e)
        finally:
            if not self._session:
                await session.close()
        self.connected = False
        return False

    async def is_authenticated(self) -> bool:
        """Check if go-librespot has an active Spotify session (zeroconf paired).
        200 = paired/authenticated, 204 = awaiting pairing."""
        session = self._session or aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=3))
        try:
            async with session.get(f'{LIBRESPOT_BASE}/status') as resp:
                return resp.status == 200
        except Exception:
            return False
        finally:
            if not self._session:
                await session.close()

    async def wait_for_ready(self, timeout=30) -> bool:
        """Wait for go-librespot to become reachable (e.g. after boot)."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if await self.check_available():
                return True
            await asyncio.sleep(2)
        log.warning("go-librespot not reachable after %ds", timeout)
        return False

    # -- Playback commands --

    async def play(self, uri: str, skip_to_uri: str | None = None) -> bool:
        """Start playing a Spotify URI (playlist, album, track).
        skip_to_uri: track URI to start at within a collection."""
        body = {'uri': uri}
        if skip_to_uri:
            body['skip_to_uri'] = skip_to_uri
        return await self._post('/player/play', body)

    async def resume(self) -> bool:
        return await self._post('/player/resume')

    async def pause(self) -> bool:
        return await self._post('/player/pause')

    async def next_track(self) -> bool:
        return await self._post('/player/next')

    async def prev_track(self) -> bool:
        return await self._post('/player/prev')

    async def stop_playback(self) -> bool:
        return await self._post('/player/pause')

    async def status(self) -> dict | None:
        """Get full player status including current track."""
        return await self._get('/status')

    # -- Internal HTTP helpers --

    async def _post(self, path, body=None) -> bool:
        if not self._session:
            return False
        try:
            async with self._session.post(
                f'{LIBRESPOT_BASE}{path}',
                json=body if body else None,
            ) as resp:
                return resp.status == 200
        except Exception as e:
            log.warning("go-librespot POST %s failed: %s", path, e)
            return False

    async def _get(self, path) -> dict | None:
        if not self._session:
            return None
        try:
            async with self._session.get(f'{LIBRESPOT_BASE}{path}') as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            log.warning("go-librespot GET %s failed: %s", path, e)
        return None

    # -- WebSocket event stream --

    async def _event_loop(self):
        """Subscribe to go-librespot WebSocket events and forward to callback."""
        retry_delay = 1
        while True:
            try:
                async with self._session.ws_connect(
                    f'ws://localhost:{LIBRESPOT_PORT}/events'
                ) as ws:
                    log.info("go-librespot event stream connected")
                    retry_delay = 1
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_ws_message(msg.data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                          aiohttp.WSMsgType.ERROR):
                            break
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("go-librespot WS error: %s (retry in %ds)",
                            e, retry_delay)

            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)

    async def _handle_ws_message(self, raw: str):
        """Parse a go-librespot WebSocket event and forward to callback."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        event_type = data.get('type')
        event_data = data.get('data', {})
        if event_type and self._on_event:
            try:
                await self._on_event(event_type, event_data)
            except Exception as e:
                log.warning("Event callback error for %s: %s", event_type, e)
