"""
Spotify Connect control via the Web API.

For network players that can't consume Spotify ShareLinks (BlueSound, HEOS,
WiiM), the BS5c drives the speaker's *built-in* Spotify Connect receiver
through the Spotify Web API instead of handing a share URI to the player:
enumerate Connect devices, pick the target, and start/transfer playback there.

Requires Spotify Premium (Connect control is Premium-only) and the target
speaker logged into the same Spotify account. Reuses the source's existing
PKCE OAuth token (scopes user-read/modify-playback-state are already granted).

EXPERIMENTAL — not yet validated against real hardware / a Premium account.
"""

import json
import logging

import aiohttp

log = logging.getLogger('beo-source-spotify')

API = "https://api.spotify.com/v1"


class SpotifyConnect:
    def __init__(self, auth, preferred_name: str = ""):
        self._auth = auth
        self._preferred = (preferred_name or "").strip().lower()
        self._device_id = None
        self._session = None

    def _get_session(self):
        # Reuse one session for the service lifetime instead of opening a fresh
        # one per request (correct but wasteful — one TCP handshake each call).
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _req(self, method: str, path: str, *, params=None, body=None):
        """Return (status, parsed_json_or_None). Never raises."""
        try:
            token = await self._auth.get_token()
        except Exception as e:
            log.warning("Spotify Connect: no token: %s", e)
            return 0, None
        try:
            async with self._get_session().request(
                method, f"{API}{path}", params=params, json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=10)) as resp:
                text = await resp.text()
                data = None
                if text:
                    try:
                        data = json.loads(text)
                    except ValueError:
                        data = None
                return resp.status, data
        except Exception as e:
            log.warning("Spotify Connect %s %s failed: %s", method, path, e)
            return 0, None

    async def list_devices(self) -> list:
        status, data = await self._req("GET", "/me/player/devices")
        if status != 200 or not data:
            return []
        return data.get("devices", [])

    async def resolve_device_id(self, refresh: bool = False):
        """Pick the target Connect device: preferred name > active > first.

        A device only appears here when it's awake and Connect-visible, so an
        offline speaker resolves to None (caller surfaces 'no active device')."""
        if self._device_id and not refresh:
            return self._device_id
        devices = await self.list_devices()
        chosen = None
        if self._preferred:
            chosen = next((d for d in devices
                           if self._preferred in (d.get("name") or "").lower()), None)
        if not chosen:
            chosen = next((d for d in devices if d.get("is_active")), None)
        if not chosen and devices:
            chosen = devices[0]
        self._device_id = chosen.get("id") if chosen else None
        if self._device_id:
            log.info("Spotify Connect target: %s", chosen.get("name"))
        else:
            log.warning("Spotify Connect: no available device "
                        "(is the speaker on and logged into Spotify?)")
        return self._device_id

    async def play(self, context_uri=None, uris=None, offset=None) -> bool:
        """Start playback of a context (playlist) or explicit track URIs on the
        target device. offset is a 0-based track index within the context."""
        device_id = await self.resolve_device_id(refresh=True)
        if not device_id:
            return False
        body = {}
        if context_uri:
            body["context_uri"] = context_uri
        if uris:
            body["uris"] = uris
        if offset is not None:
            body["offset"] = {"position": int(offset)}
        status, _ = await self._req(
            "PUT", "/me/player/play", params={"device_id": device_id}, body=body)
        return status in (200, 202, 204)

    async def _transport(self, method: str, path: str) -> bool:
        device_id = await self.resolve_device_id()
        params = {"device_id": device_id} if device_id else None
        status, _ = await self._req(method, path, params=params)
        return status in (200, 202, 204)

    async def next(self) -> bool:
        return await self._transport("POST", "/me/player/next")

    async def previous(self) -> bool:
        return await self._transport("POST", "/me/player/previous")

    async def pause(self) -> bool:
        return await self._transport("PUT", "/me/player/pause")

    async def resume(self) -> bool:
        return await self._transport("PUT", "/me/player/play")

    async def currently_playing(self):
        """Return the current track dict from the Web API, or None."""
        status, data = await self._req("GET", "/me/player")
        if status != 200 or not data:
            return None
        item = data.get("item") or {}
        album = item.get("album") or {}
        images = album.get("images") or []
        return {
            "is_playing": bool(data.get("is_playing")),
            "title": item.get("name", ""),
            "artist": ", ".join(a.get("name", "") for a in item.get("artists", [])),
            "album": album.get("name", ""),
            "artwork": images[0]["url"] if images else "",
            "uri": item.get("uri", ""),
        }
