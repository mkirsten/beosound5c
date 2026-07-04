#!/usr/bin/env python3
"""
BeoSound 5c Beo6 Remote Service (beo-beo6)

Emulates a BeoMaster 5's BeoNet XMPP interface so a Beo6 remote control
can browse and control one configured source on the BS5c.

Protocol: XMPP (jabber:client) over TCP port 5222, no TLS.
Custom namespaces: beonet:content, beonet:player, beonet:renderer, beonet:power.
Cover art served via HTTP on port 8080.

Port: 5222 (XMPP), 8080 (cover art)
"""

import asyncio
import hashlib
import html
import logging
import os
import signal
import socket
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote

import io

import aiohttp
from aiohttp import web
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.background_tasks import BackgroundTaskSet
from lib.config import cfg
from lib.endpoints import PLAYER_PORT, ROUTER_PORT
from lib.loop_monitor import LoopMonitor
from lib.watchdog import watchdog_loop

logging.basicConfig(level=logging.DEBUG, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-beo6')
# Quiet down noisy libraries
logging.getLogger('aiohttp').setLevel(logging.WARNING)
logging.getLogger('zeroconf').setLevel(logging.WARNING)

# Ports
XMPP_PORT = 5222
ART_PORT = 8080

# Internal BS5c service URLs.  beo6 uses these as base URLs with many
# dynamic paths, so we keep the bare base rather than importing every
# individual endpoint constant.
ROUTER_URL = f"http://localhost:{ROUTER_PORT}"
PLAYER_URL = f"http://localhost:{PLAYER_PORT}"

# BeoNet identity
DEVICE_SERIAL = cfg("beo6", "serial", default="00000001")
DEVICE_NAME = cfg("beo6", "name", default="BeoSound5")
JID = f"{DEVICE_NAME}-{DEVICE_SERIAL}@products.bang-olufsen.com"


def _get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


class _LRUMap(dict):
    """Bounded dict for artwork blobs — evicts the oldest entry when full.

    Values are base64 data URIs up to a few hundred KB each; unbounded
    growth (one entry per track change, never read back for old tracks)
    slowly OOMs a Pi over months.
    """

    def __init__(self, cap=200):
        super().__init__()
        self._cap = cap

    def __setitem__(self, key, value):
        if key in self:
            del self[key]  # re-insert refreshes recency (dicts keep insertion order)
        elif len(self) >= self._cap:
            del self[next(iter(self))]
        super().__setitem__(key, value)


def _esc(text):
    """Escape text for XML attribute/content."""
    return html.escape(str(text), quote=True)


class BeoNetSession:
    """Handles one Beo6 XMPP connection."""

    def __init__(self, reader, writer, service):
        self.reader = reader
        self.writer = writer
        self.service = service
        self.peer_jid = ""
        self.subscriptions = set()  # ("renderer", "audio_only_renderer"), ("player", "NMUSIC")
        self._renderer_seq = 0
        self._player_seq = 0
        self._buf = b""
        self._closed = False

    async def run(self):
        peer = self.writer.get_extra_info('peername')
        log.info("Beo6 connected from %s", peer)
        try:
            while not self._closed:
                data = await self.reader.read(8192)
                if not data:
                    break
                self._buf += data
                await self._process_buffer()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._closed = True
            self.writer.close()
            self.service.remove_session(self)
            log.info("Beo6 disconnected from %s", peer)

    async def _process_buffer(self):
        """Process complete XML stanzas from the buffer."""
        text = self._buf.decode('utf-8', errors='replace')
        log.debug("Buffer (%d bytes): %.300s", len(text), text)

        # Handle stream open
        if text.startswith('<?xml'):
            idx = text.find('?>')
            if idx >= 0:
                text = text[idx + 2:]
        if '<stream:stream' in text:
            idx = text.find('>')
            if idx < 0:
                return  # incomplete
            stream_open = text[:idx + 1]
            text = text[idx + 1:]
            self._buf = text.encode('utf-8')
            await self._handle_stream_open(stream_open)
            if text:
                await self._process_buffer()
            return

        # Handle stream close
        if '</stream:stream>' in text:
            self._closed = True
            return

        # Try to parse complete stanzas (iq, presence, message)
        while text:
            text = text.strip()
            if not text:
                self._buf = b''
                break
            stanza, rest = self._extract_stanza(text)
            if stanza is None:
                if rest != text:
                    # Garbage at the buffer head was consumed — persist the
                    # skip and keep parsing.  Without this, one unknown tag
                    # (or a UTF-8 char split across TCP segments) stays at
                    # the head forever and wedges the whole session.
                    text = rest
                    self._buf = text.encode('utf-8')
                    continue
                break  # incomplete stanza — wait for more bytes
            text = rest
            self._buf = text.encode('utf-8')
            try:
                await self._handle_stanza(stanza)
            except Exception as e:
                # A malformed attribute (e.g. non-numeric int() input from
                # the remote) must cost one stanza, not the whole session.
                log.warning("Stanza handler error: %s — %.200s", e, stanza)

    def _extract_stanza(self, text):
        """Extract one complete XML stanza from text. Returns (stanza_str, remaining) or (None, text)."""
        for tag in ('iq', 'presence', 'message'):
            if text.startswith(f'<{tag}') and (
                len(text) > len(tag) + 1 and text[len(tag) + 1] in (' ', '>', '/')):
                end_full = text.find(f'</{tag}>')
                if end_full >= 0:
                    end = end_full + len(f'</{tag}>')
                    return text[:end], text[end:]
                # Check for self-closing (no children) — only if no opening child tags
                end_sc = text.find('/>')
                if end_sc >= 0 and end_sc < text.find('>', 1):
                    # Self-closing top-level tag
                    end = end_sc + 2
                    return text[:end], text[end:]
                return None, text  # incomplete
        # Unknown or garbage at the head — skip to the next tag start so
        # the caller can make progress (returning the input unchanged means
        # "incomplete, wait for more bytes", which garbage never satisfies).
        log.warning("Unexpected XMPP data, skipping: %.100s", text)
        nxt = text.find('<', 1)
        return None, text[nxt:] if nxt > 0 else ""

    async def _handle_stream_open(self, data):
        """Handle <stream:stream> open from Beo6."""
        # Extract 'from' attribute
        if 'from="' in data:
            start = data.index('from="') + 6
            end = data.index('"', start)
            self.peer_jid = data[start:end]
        log.info("Stream open from %s", self.peer_jid)

        # Send stream open + initial disco query
        resp = (
            f'<stream:stream xmlns="jabber:client" '
            f'xmlns:stream="http://etherx.jabber.org/streams" '
            f'from="{_esc(JID)}" to="{_esc(self.peer_jid)}" version="1.0">'
        )
        # Immediately send disco#info query (like the real BM5)
        resp += (
            f'<iq id="16" to="{_esc(self.peer_jid)}" '
            f'from="{_esc(JID)}" type="get">'
            f'<query xmlns="http://jabber.org/protocol/disco#info"></query></iq>'
        )
        await self._send(resp)

    async def _handle_stanza(self, text):
        """Route an XMPP stanza."""
        log.debug("XMPP RX ← %.300s", text)
        try:
            # Wrap in a root element for parsing
            xml = f'<root xmlns:stream="http://etherx.jabber.org/streams">{text}</root>'
            root = ET.fromstring(xml)
            stanza = root[0]
        except ET.ParseError as e:
            log.warning("XML parse error: %s — %.200s", e, text)
            return

        tag = stanza.tag
        if tag == 'iq':
            await self._handle_iq(stanza)
        elif tag == 'presence':
            await self._handle_presence(stanza)
        elif tag == 'message':
            pass  # Beo6 doesn't send messages to BM5

    async def _handle_presence(self, el):
        """Handle Beo6 presence announcement."""
        log.info("Beo6 presence: %s", self.peer_jid)
        # No response needed — the BM5 just records the peer

    async def _handle_iq(self, el):
        iq_type = el.get('type', '')
        iq_id = el.get('id', '')
        log.debug("IQ type=%s id=%s children=%d", iq_type, iq_id, len(el))

        if iq_type == 'result':
            # Beo6's response to our disco query — ignore
            log.debug("Ignoring IQ result id=%s", iq_id)
            return

        if iq_type == 'get':
            child = el[0] if len(el) else None
            if child is None:
                log.warning("IQ get with no child element")
                return
            # Handle both {namespace}tag and plain tag formats
            raw_tag = child.tag
            if '}' in raw_tag:
                xmlns = raw_tag.split('{')[1].split('}')[0]
                ns = raw_tag.split('}')[1]
            else:
                ns = raw_tag
                xmlns = child.get('xmlns', '')
            log.debug("IQ get child: tag=%s ns=%s xmlns=%s", raw_tag, ns, xmlns)

            if xmlns == 'http://jabber.org/protocol/disco#info' or ns == 'query' and child.get('xmlns', '') == 'http://jabber.org/protocol/disco#info':
                await self._handle_disco(iq_id)
            elif xmlns == 'beonet:renderer' or child.get('xmlns', '') == 'beonet:renderer':
                await self._handle_renderer_get(iq_id, child)
            elif xmlns == 'beonet:content' or child.get('xmlns', '') == 'beonet:content':
                await self._handle_content_query(iq_id, child)
            elif (xmlns == 'beonet:player' or child.get('xmlns', '') == 'beonet:player') and ns == 'query-queue':
                await self._handle_queue_query(iq_id, child)
            elif xmlns == 'urn:xmpp:ping' or child.get('xmlns', '') == 'urn:xmpp:ping':
                await self._send_iq_result(iq_id, '')
            else:
                log.info("Unhandled IQ get: tag=%s ns=%s xmlns=%s", raw_tag, ns, xmlns)
                await self._send_iq_result(iq_id, '')

        elif iq_type == 'set':
            child = el[0] if len(el) else None
            if child is None:
                log.warning("IQ set with no child element")
                return
            raw_tag = child.tag
            if '}' in raw_tag:
                xmlns = raw_tag.split('{')[1].split('}')[0]
                ns = raw_tag.split('}')[1]
            else:
                ns = raw_tag
                xmlns = child.get('xmlns', '')
            log.debug("IQ set child: tag=%s ns=%s xmlns=%s", raw_tag, ns, xmlns)

            if ns == 'status-subscribe':
                await self._handle_subscribe(iq_id, child)
            elif ns == 'replace_after' or ns == 'replace-after':
                await self._handle_play(iq_id, child)
            elif ns == 'skip':
                await self._handle_skip(iq_id, child)
            else:
                log.info("Unhandled IQ set: tag=%s ns=%s xmlns=%s", raw_tag, ns, xmlns)
                await self._send_iq_result(iq_id, '')

    async def _handle_disco(self, iq_id):
        """Respond to disco#info with our capabilities."""
        body = (
            '<query xmlns="http://jabber.org/protocol/disco#info">'
            f'<identity category="client" name="{_esc(DEVICE_NAME)}" type="product"></identity>'
            '<feature var="beonet:content"></feature>'
            '<feature var="beonet:player"></feature>'
            '<feature var="beonet:power"></feature>'
            '<feature var="beonet:renderer"></feature>'
            '<feature var="jid\\20escaping"></feature>'
            '</query>'
        )
        await self._send_iq_result(iq_id, body)

    async def _handle_subscribe(self, iq_id, el):
        """Handle status-subscribe for renderer or player."""
        # ET consumes xmlns into the tag as {namespace}tag, so extract from there
        raw_tag = el.tag
        if '}' in raw_tag:
            xmlns = raw_tag.split('{')[1].split('}')[0]
        else:
            xmlns = el.get('xmlns', '')
        iid = el.get('iid', '')
        log.info("Subscribe: %s iid=%s", xmlns, iid)

        if xmlns == 'beonet:renderer':
            self.subscriptions.add(('renderer', iid))
            # Wait briefly for initial state sync if content_id is still 0
            if self.service.content_id == "0" and self.service._all_tracks:
                await asyncio.sleep(0.5)
            status = await self.service.get_renderer_status()
            body = (
                f'<status seq-nr="0" iid="{_esc(iid)}" xmlns="beonet:renderer">'
                f'{status}</status>'
            )
            await self._send_iq_result(iq_id, body)

        elif xmlns == 'beonet:player':
            self.subscriptions.add(('player', iid))
            body = (
                f'<status seq-nr="0" iid="{_esc(iid)}" xmlns="beonet:player">'
                f'<status state="ready" pq-revision="1"></status></status>'
            )
            await self._send_iq_result(iq_id, body)

    async def _handle_renderer_get(self, iq_id, el):
        """Handle renderer status poll."""
        iid = el.get('iid', 'audio_only_renderer')
        status = await self.service.get_renderer_status()
        body = (
            f'<status seq-nr="{self._renderer_seq}" iid="{_esc(iid)}" '
            f'xmlns="beonet:renderer">{status}</status>'
        )
        await self._send_iq_result(iq_id, body)

    async def _handle_content_query(self, iq_id, el):
        """Handle beonet:content queries — browse the configured source."""
        content_type = el.get('type', '')  # track, album, album-artist
        first = int(el.get('first', '0'))
        last = int(el.get('last', '0'))
        profile = el.get('profile', '')

        # Extract filters and ordering
        filters = {}
        order_by = None
        order_sort = 'asc'
        seed_key = None
        seed_value = None
        attrs = []

        for child in el:
            tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            if tag == 'static_filter':
                filters[child.get('attr', '')] = {
                    'value': child.get('value', ''),
                    'opr': child.get('opr', 'eq'),
                }
            elif tag == 'order_by':
                order_by = child.get('attr', '')
                order_sort = child.get('sort', 'asc')
            elif tag == 'seed':
                seed_key = child.get('key', '')
                seed_value = child.get('value', '')
            elif tag == 'attr':
                attrs.append(child.get('name', ''))

        log.info("Content query: type=%s first=%d last=%d filters=%s order=%s seed=%s/%s attrs=%s",
                 content_type, first, last, filters, order_by, seed_key, seed_value, attrs)

        result = await self.service.query_content(
            content_type, first, last, filters, order_by, order_sort, attrs,
            seed_key, seed_value)
        await self._send_iq_result(iq_id, result)

    async def _handle_queue_query(self, iq_id, el):
        """Handle play queue query — parse requested attrs from nested <attr> elements."""
        queue_id = el.get('queue-id', '')
        pos = int(el.get('pos', '0'))
        first_offset = int(el.get('first-offset', '0'))
        last_offset = int(el.get('last-offset', '1'))

        # Parse nested <attr name="id"><attr name="track.id"/><attr name="track.title"/>...</attr>
        requested_attrs = []
        for outer in el:
            tag = outer.tag.split('}')[-1] if '}' in outer.tag else outer.tag
            if tag == 'attr':
                for inner in outer:
                    inner_tag = inner.tag.split('}')[-1] if '}' in inner.tag else inner.tag
                    if inner_tag == 'attr':
                        requested_attrs.append(inner.get('name', ''))
        log.debug("Queue query: queue_id=%s pos=%s offsets=%s-%s attrs=%s",
                  queue_id, pos, first_offset, last_offset, requested_attrs)

        result = await self.service.query_queue(queue_id, pos, first_offset, last_offset, requested_attrs)
        await self._send_iq_result(iq_id, result)

    async def _handle_play(self, iq_id, el):
        """Handle replace_after — play a track."""
        # Extract track ID from filters (may be namespace-qualified)
        track_id = None
        filters_el = None
        for child in el:
            tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            if tag == 'filters':
                filters_el = child
                break
        if filters_el is not None:
            for f in filters_el:
                if f.get('attr') == 'id':
                    track_id = f.get('value')
                    break

        log.info("Play command: track_id=%s", track_id)

        # Set state to playing immediately (BM5 behavior)
        self.service.state = "playing"

        # Detect queue items (q:N) vs catalog items (integer IDs)
        if track_id and track_id.startswith("q:"):
            # Queue item — route to router's queue play endpoint
            position = int(track_id.split(":", 1)[1])
            log.info("Queue play: track_id=%s -> absolute position=%d", track_id, position)
            self.service.pq_revision += 1
            self.service.queue_id = str(self.service.pq_revision)
            self.service.queue_position = 0
            self.service._play_suppress_until = time.monotonic() + 5
            try:
                async with self.service._http.post(
                    f"{ROUTER_URL}/router/queue/play",
                    json={"position": position},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        log.info("Queue play position %d: ok", position)
                    else:
                        log.warning("Queue play position %d: HTTP %d", position, resp.status)
            except Exception as e:
                log.warning("Queue play failed: %s", e)
        else:
            # Catalog item — existing behavior
            if track_id:
                self.service.content_id = "0"  # updated on next queue query
                self.service.pq_revision += 1
                self.service.queue_id = str(self.service.pq_revision)
                self.service.queue_position = 0

            # Suppress WS updates briefly to prevent content-id flipping
            self.service._play_suppress_until = time.monotonic() + 5

            await self.service.play_track(track_id)

        # Respond with command ack
        body = '<command xmlns="beonet:player"></command>'
        await self._send_iq_result(iq_id, body)

        # Push delta updates (no state — BM5 behavior)
        await self._push_player_status()
        status_xml = await self.service._build_renderer_status_delta()
        await self.push_renderer_update(status_xml)

    async def _handle_skip(self, iq_id, el):
        """Handle skip — jump by offset in the queue."""
        offset = int(el.get('offset', '0'))
        queue_id = el.get('queue-id', '')
        log.info("Skip command: offset=%d queue-id=%s", offset, queue_id)

        if offset == 0 and queue_id:
            # Beo6 encodes clicked track's router queue position in queue-id
            position = int(queue_id)
            log.info("Skip play: queue position=%d", position)
            self.service.state = "playing"
            self.service.pq_revision += 1
            self.service.queue_id = str(self.service.pq_revision)
            self.service.queue_position = 0
            self.service._play_suppress_until = time.monotonic() + 5
            try:
                async with self.service._http.post(
                    f"{ROUTER_URL}/router/queue/play",
                    json={"position": position},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    log.info("Queue play position %d -> %d", position, resp.status)
            except Exception as e:
                log.error("Queue play failed: %s", e)
        elif offset == 0:
            pass  # no track id — no-op
        elif offset == 1:
            await self.service.skip_next()
        elif offset == -1:
            await self.service.skip_prev()
        else:
            # Multi-track skip: jump to absolute queue position
            await self.service.skip_to_offset(offset)

        body = '<command xmlns="beonet:player"></command>'
        await self._send_iq_result(iq_id, body)

        # Push delta updates (no state — BM5 behavior)
        await self._push_player_status()
        status_xml = await self.service._build_renderer_status_delta()
        await self.push_renderer_update(status_xml)

    # -- Push notifications --

    async def push_renderer_update(self, status_xml):
        """Push a renderer status update to this Beo6."""
        if self._closed:
            return
        self._renderer_seq += 1
        msg = (
            f'<message to="{_esc(self.peer_jid)}" from="{_esc(JID)}">'
            f'<body><status xmlns="beonet:renderer" iid="audio_only_renderer" '
            f'seq-nr="{self._renderer_seq}">{status_xml}</status></body></message>'
        )
        await self._send(msg)

    async def push_player_update(self, pq_revision):
        """Push a player status update to this Beo6."""
        if self._closed:
            return
        self._player_seq += 1
        msg = (
            f'<message to="{_esc(self.peer_jid)}" from="{_esc(JID)}">'
            f'<body><status xmlns="beonet:player" iid="NMUSIC" '
            f'seq-nr="{self._player_seq}">'
            f'<status pq-revision="{pq_revision}"></status>'
            f'</status></body></message>'
        )
        await self._send(msg)

    async def _push_renderer_status(self):
        """Push full renderer status."""
        status = await self.service.get_renderer_status()
        await self.push_renderer_update(status)

    async def _push_player_status(self):
        """Push player queue revision."""
        await self.push_player_update(self.service.pq_revision)

    # -- Low-level send --

    async def _send_iq_result(self, iq_id, body):
        xml = (
            f'<iq id="{_esc(iq_id)}" to="{_esc(self.peer_jid)}" '
            f'from="{_esc(JID)}" type="result">{body}</iq>'
        )
        await self._send(xml)

    async def _send(self, data):
        if self._closed:
            return
        log.debug("XMPP TX → %.500s", data)
        try:
            self.writer.write(data.encode('utf-8'))
            # The Beo6 is a battery WiFi remote that sleeps/roams — a
            # black-holed connection makes drain() hang for the TCP
            # retransmit timeout (15-30 min), stalling every caller up
            # the chain (including the router media-WS loop).
            await asyncio.wait_for(self.writer.drain(), timeout=5)
        except asyncio.TimeoutError:
            log.warning("Send to Beo6 timed out — dropping session")
            self._closed = True
            transport = self.writer.transport
            if transport is not None:
                transport.abort()
        except (ConnectionError, OSError) as e:
            log.warning("Send failed: %s", e)
            self._closed = True


class Beo6Service:
    """Main service — manages XMPP server, content provider, and media tracking."""

    def __init__(self):
        self.sessions: list[BeoNetSession] = []
        self._http: aiohttp.ClientSession | None = None
        self._media_ws: aiohttp.ClientWebSocketResponse | None = None
        self._media_ws_task: asyncio.Task | None = None
        self._background_tasks = BackgroundTaskSet(log, label="beo6")

        # Source config
        self.source_id = cfg("beo6", "source", default="spotify")
        self.source_port = self._get_source_port()

        # Media state (from router WebSocket)
        self.state = "stopped"
        self.title = ""
        self.artist = ""
        self.album = ""
        self.artwork_url = ""
        self.track_number = 0
        self.volume = 45
        self.content_id = "0"
        self.queue_id = "1"
        self.queue_position = 0
        self.pq_revision = 1

        # Artwork hash -> URL mapping for cover art proxy (bounded)
        self._art_map = _LRUMap(cap=200)
        # Suppress media WS updates briefly after a play command
        self._play_suppress_until = 0
        # Fixed queue start index — all queue positions are relative to this
        self._queue_start_idx = 0
        # Live now-playing track from player (always authoritative for queue pos 0)
        self._now_playing = None  # dict with title, artist, album_title, image, id

        # Content cache (playlists/tracks from source)
        self._playlists = []
        self._all_tracks = []   # flattened track list for content queries
        self._artists = {}      # artist_name -> artist_id
        self._albums = {}       # album_title -> {id, artist_id, tracks, image}
        self._content_revision = 0
        self._last_content_fetch = 0

    def _get_source_port(self):
        """Get the HTTP port for the configured source."""
        ports = {
            'spotify': 8771,
            'plex': 8774,
            'radio': 8773,
            'usb': 8775,
            'tidal': 8776,
            'apple_music': 8777,
        }
        return ports.get(self.source_id, 8771)

    def remove_session(self, session):
        if session in self.sessions:
            self.sessions.remove(session)

    async def start(self):
        """Start the XMPP server, cover art server, and mDNS advertisement."""
        # Check if beo6 is configured
        beo6_cfg = cfg("beo6")
        if beo6_cfg is None:
            log.info("No beo6 config — exiting")
            from lib.watchdog import sd_notify
            sd_notify("READY=1\nSTATUS=Not configured, exiting")
            sd_notify("STOPPING=1")
            sys.exit(0)

        self._http = aiohttp.ClientSession()

        # Start XMPP server
        server = await asyncio.start_server(
            self._handle_connection, '0.0.0.0', XMPP_PORT)
        log.info("XMPP server on port %d", XMPP_PORT)

        # Start cover art HTTP server
        art_app = web.Application()
        art_app.router.add_get('/', self._handle_art_request)
        art_runner = web.AppRunner(art_app)
        await art_runner.setup()
        art_site = web.TCPSite(art_runner, '0.0.0.0', ART_PORT)
        await art_site.start()
        log.info("Cover art server on port %d", ART_PORT)

        # Start mDNS advertisement
        self._background_tasks.spawn(self._advertise_mdns(), name="advertise_mdns")

        # Subscribe to router media updates (kept separate so we can reconnect)
        self._media_ws_task = self._background_tasks.spawn(
            self._media_ws_loop(), name="media_ws_loop")

        # Initial content fetch + state sync
        self._background_tasks.spawn(self._fetch_content(), name="fetch_content_initial")
        self._background_tasks.spawn(self._sync_initial_state(), name="sync_initial_state")

        # Watchdog — kept as a bare create_task since it runs for the
        # process lifetime and lib/watchdog.py has no exception surface
        # worth tracking.
        asyncio.create_task(watchdog_loop())

        log.info("Beo6 service ready (source=%s, jid=%s)", self.source_id, JID)

    async def _handle_connection(self, reader, writer):
        session = BeoNetSession(reader, writer, self)
        self.sessions.append(session)
        await session.run()

    # -- mDNS --

    async def _advertise_mdns(self):
        """Advertise _beonet._tcp and _boproduct._tcp via zeroconf."""
        try:
            from zeroconf.asyncio import AsyncZeroconf
            from zeroconf import ServiceInfo
        except ImportError:
            log.warning("zeroconf not installed — mDNS advertisement disabled. "
                        "Install with: pip install zeroconf")
            return

        local_ip = _get_local_ip()
        hostname = socket.gethostname()
        ip_bytes = socket.inet_aton(local_ip)
        mac = cfg("beo6", "mac", default="00-00-00-00-00-01")

        azc = AsyncZeroconf()
        zc = azc.zeroconf

        # _beonet._tcp service
        beonet_info = ServiceInfo(
            "_beonet._tcp.local.",
            f"{JID}._beonet._tcp.local.",
            addresses=[ip_bytes],
            port=XMPP_PORT,
            properties={
                'hash': 'sha-1',
                'node': 'beonet',
                'name': DEVICE_NAME,
                'jid': JID,
                'txtvers': '1',
                'ver': 'NgEY77Raons6iL3skQWwYhhJxDs=',
            },
            server=f"{hostname}.local.",
        )

        # _boproduct._tcp service (real BM5 uses _boproduct_-1 but zeroconf rejects it)
        boproduct_info = ServiceInfo(
            "_boproduct._tcp.local.",
            f"{DEVICE_NAME} [{mac}]._boproduct._tcp.local.",
            addresses=[ip_bytes],
            port=30000,
            properties={
                'CLUSTER_ID': '-1',
                'INTERFACE_TYPE': 'RMIAVN::IRMIAVNProduct',
                'LOCATION': '',
                'MAC_ADDRESS': mac,
                'NTP_REFERENCE': '-1',
                'PRODUCT_ID': 'BeoMaster 5',
                'PRODUCT_NAME': DEVICE_NAME,
                'PRODUCT_SERIALNO': DEVICE_SERIAL,
                'SHARED_FOLDER_NAME': 'BM-Share$',
                'SW_VERSION': '7.04.01.1945',
                'UPTIME': '0',
            },
            server=f"{hostname}.local.",
        )

        try:
            await azc.async_register_service(beonet_info)
            await azc.async_register_service(boproduct_info)
            log.info("mDNS: registered _beonet._tcp and _boproduct._tcp on %s", local_ip)
        except Exception as e:
            log.error("mDNS registration failed: %s", e, exc_info=True)

    async def _sync_initial_state(self):
        """Fetch current playback state from router on startup."""
        # Wait for content to load first
        for _ in range(30):
            if self._all_tracks:
                break
            await asyncio.sleep(1)

        try:
            async with self._http.get(
                f"{ROUTER_URL}/router/status",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.volume = data.get('volume', self.volume)
                    media = data.get('media', {})
                    if media and media.get('title'):
                        self.state = media.get('state', self.state)
                        self.title = media.get('title', '')
                        self.artist = media.get('artist', '')
                        self.album = media.get('album', '')
                        self.track_number = media.get('track_number', 0)
                        if media.get('artwork'):
                            self.artwork_url = media['artwork']
                        # Build now-playing from live metadata
                        image_ref = ''
                        if self.artwork_url:
                            if self.artwork_url.startswith('data:'):
                                art_hash = hashlib.md5(f"np_{self.title}_{self.artist}".encode()).hexdigest()
                                try:
                                    b64_data = self.artwork_url.split(',', 1)[1]
                                    self._art_map[art_hash] = f"base64:{b64_data}"
                                    image_ref = f"synth:{art_hash}"
                                except (IndexError, Exception):
                                    pass
                            else:
                                image_ref = self.artwork_url
                        self.pq_revision += 1
                        self.queue_id = str(self.pq_revision)
                        # Query router for absolute current_index
                        try:
                            async with self._http.get(
                                f"{ROUTER_URL}/router/queue?start=0&max_items=1",
                                timeout=aiohttp.ClientTimeout(total=3),
                            ) as qresp:
                                if qresp.status == 200:
                                    qdata = await qresp.json()
                                    ci = qdata.get("current_index", -1)
                                    if ci >= 0:
                                        self.content_id = str(ci)
                                    else:
                                        self.content_id = "0"
                                else:
                                    self.content_id = "0"
                        except Exception:
                            self.content_id = "0"
                        self._now_playing = {
                            'id': self.content_id,
                            'title': self.title,
                            'artist': self.artist,
                            'album_title': self.album,
                            'image': image_ref,
                            'index': self.track_number,
                        }
                        log.info("Initial state: %s (%s - %s)",
                                 self.state, self.artist, self.title)
        except Exception as e:
            log.warning("Failed to sync initial state: %s", e)

    # -- Router media WebSocket --

    async def _media_ws_loop(self):
        """Connect to router WebSocket and track media updates."""
        while True:
            try:
                async with self._http.ws_connect(
                    f"{ROUTER_URL}/router/ws",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as ws:
                    log.info("Connected to router WebSocket")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_media_update(msg.json())
                        elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                          aiohttp.WSMsgType.ERROR):
                            break
            except Exception as e:
                log.warning("Router WS error: %s — retrying in 5s", e)
            await asyncio.sleep(5)

    async def _handle_media_update(self, data):
        """Process a media update from the router."""
        if data.get('type') != 'media_update':
            return
        if time.monotonic() < self._play_suppress_until:
            return
        media = data.get('data', {})

        new_title = media.get('title', '')
        new_artist = media.get('artist', '')
        if not new_title:
            return

        old_title = self.title
        old_artist = self.artist
        self.state = media.get('state', self.state)
        self.title = new_title
        self.artist = new_artist
        self.album = media.get('album', self.album)
        self.track_number = media.get('track_number', 0)
        if media.get('artwork'):
            self.artwork_url = media['artwork']

        # Detect track change
        if new_title == old_title and new_artist == old_artist:
            return

        # Build artwork reference for the now-playing track
        image_ref = ''
        if self.artwork_url:
            if self.artwork_url.startswith('data:'):
                # Store base64 artwork in art_map for the HTTP proxy
                art_hash = hashlib.md5(f"np_{new_title}_{new_artist}".encode()).hexdigest()
                try:
                    b64_data = self.artwork_url.split(',', 1)[1]
                    self._art_map[art_hash] = f"base64:{b64_data}"
                    image_ref = f"synth:{art_hash}"
                except (IndexError, Exception):
                    pass
            else:
                image_ref = self.artwork_url

        # Try to find track in catalog for queue continuity
        track_id = self._find_track_id(self.title, self.artist)
        self.pq_revision += 1
        self.queue_id = str(self.pq_revision)
        self.queue_position = 0

        if track_id is not None:
            for i, t in enumerate(self._all_tracks):
                if t['id'] == track_id:
                    self._queue_start_idx = i
                    break
            log.info("Now playing (catalog %d): %s - %s", track_id, self.artist, self.title)
        else:
            self._queue_start_idx = -1
            log.info("Now playing (live): %s - %s", self.artist, self.title)

        # Query router for absolute current_index
        try:
            async with self._http.get(
                f"{ROUTER_URL}/router/queue?start=0&max_items=1",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as qresp:
                if qresp.status == 200:
                    qdata = await qresp.json()
                    ci = qdata.get("current_index", -1)
                    if ci >= 0:
                        self.content_id = str(ci)
                    else:
                        self.content_id = "0"
                else:
                    self.content_id = "0"
        except Exception:
            self.content_id = "0"

        self._now_playing = {
            'id': self.content_id,
            'title': self.title,
            'artist': self.artist,
            'album_title': self.album,
            'image': image_ref,
            'index': self.track_number,
        }

        # Push to all connected Beo6 remotes
        status_xml = await self._build_renderer_status_delta()
        for session in self.sessions:
            try:
                await session.push_renderer_update(status_xml)
                await session.push_player_update(self.pq_revision)
            except Exception:
                pass

    async def _build_renderer_status_delta(self):
        """Build a delta renderer status XML for push updates.
        Note: BM5 does NOT include state= in delta pushes — only content/queue info."""
        return (
            f'<status '
            f'content-peer="{_esc(JID)}" '
            f'renderer-instance-id="NMUSIC" '
            f'content-type="track" '
            f'content-id="{_esc(self.content_id)}" '
            f'queue-id="{_esc(self.queue_id)}" '
            f'queue-position="{self.queue_position}"></status>'
        )

    # -- Renderer status --

    async def get_renderer_status(self):
        """Build full renderer status XML."""
        state_map = {'playing': 'playing', 'paused': 'stopped', 'stopped': 'stopped'}
        beo_state = state_map.get(self.state, 'stopped')

        # Get volume from router
        try:
            async with self._http.get(
                f"{ROUTER_URL}/router/status",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.volume = data.get('volume', self.volume)
        except Exception:
            pass

        return (
            f'<status state="{beo_state}" '
            f'content-peer="{_esc(JID)}" '
            f'renderer-instance-id="NMUSIC" '
            f'content-type="track" '
            f'content-id="{_esc(self.content_id)}" '
            f'queue-id="{_esc(self.queue_id)}" '
            f'queue-position="{self.queue_position}" '
            f'playback-rate="1.000000" '
            f'volume="{self.volume}" '
            f'muted="0" treble="0" bass="0" balance="0" loudness="0"></status>'
        )

    # -- Content provider --

    async def _fetch_content(self):
        """Fetch playlists/tracks from the configured source."""
        try:
            async with self._http.get(
                f"http://localhost:{self.source_port}/playlists",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        self._playlists = data
                        self._build_content_index()
                        log.info("Loaded %d playlists, %d tracks",
                                 len(self._playlists), len(self._all_tracks))
                    elif isinstance(data, dict):
                        # Source not ready (setup_needed, loading, etc.)
                        log.info("Source not ready: %s", data)
                        await asyncio.sleep(30)
                        self._background_tasks.spawn(
                            self._fetch_content(), name="fetch_content_retry")
        except Exception as e:
            log.warning("Failed to fetch content: %s — retrying in 30s", e)
            await asyncio.sleep(30)
            self._background_tasks.spawn(
                self._fetch_content(), name="fetch_content_retry")

    def _build_content_index(self):
        """Build flat track list, artist index, and album index from playlists."""
        tracks = []
        artists = {}
        albums = {}
        artist_id = 1
        album_id = 1
        track_id = 1

        for pl in self._playlists:
            pl_name = pl.get('name', '')
            pl_image = pl.get('image', '')

            for pl_track_idx, t in enumerate(pl.get('tracks', [])):
                t_artist = t.get('artist', '') or 'Unknown'
                t_album = pl_name  # Use playlist name as album
                t_image = t.get('image', pl_image)

                # Artist
                if t_artist not in artists:
                    artists[t_artist] = {
                        'id': artist_id,
                        'name': t_artist,
                        'albums': set(),
                    }
                    artist_id += 1
                a_entry = artists[t_artist]

                # Album (= playlist)
                if t_album not in albums:
                    albums[t_album] = {
                        'id': album_id,
                        'title': t_album,
                        'artist_id': a_entry['id'],
                        'artist_name': t_artist,
                        'image': t_image,
                        'tracks': [],
                    }
                    album_id += 1
                    a_entry['albums'].add(t_album)
                alb = albums[t_album]

                tracks.append({
                    'id': track_id,
                    'title': t.get('name', ''),
                    'artist': t_artist,
                    'album_title': t_album,
                    'album_id': alb['id'],
                    'artist_id': a_entry['id'],
                    'uri': t.get('uri', ''),
                    'image': t_image,
                    'index': len(alb['tracks']),
                    'added_time': track_id * 1000,  # synthetic
                    'play_count': 0,
                    'last_played': 0,
                    '_playlist_id': pl.get('id', ''),
                    '_playlist_idx': pl_track_idx,
                })
                alb['tracks'].append(track_id)
                track_id += 1

        # Tag first track of first 3 playlists for home screen display.
        # Each query slot uses a different ordering, so assign one playlist per slot:
        #   Left (last-played-time desc) -> playlist 0
        #   Middle (added-time desc)     -> playlist 1
        #   Right (play-count desc)      -> playlist 2
        playlist_first_tracks = []
        seen_playlists = set()
        for i, t in enumerate(tracks):
            pl_id = t.get('_playlist_id')
            if pl_id and pl_id not in seen_playlists:
                seen_playlists.add(pl_id)
                playlist_first_tracks.append((i, pl_id))
                if len(playlist_first_tracks) >= 3:
                    break

        base_time = int(time.time())
        if len(playlist_first_tracks) >= 1:
            tracks[playlist_first_tracks[0][0]]['last_played'] = base_time
        if len(playlist_first_tracks) >= 2:
            tracks[playlist_first_tracks[1][0]]['added_time'] = base_time
        if len(playlist_first_tracks) >= 3:
            tracks[playlist_first_tracks[2][0]]['play_count'] = 1

        self._all_tracks = tracks
        self._artists = artists
        self._albums = albums
        self._content_revision += 1

    def _find_track_id(self, title, artist):
        """Find track ID by title + artist match (case-insensitive)."""
        if not title:
            return None
        title_l = title.lower().strip()
        artist_l = (artist or '').lower().strip()

        for t in self._all_tracks:
            if t['title'].lower().strip() == title_l and t['artist'].lower().strip() == artist_l:
                return t['id']
        return None

    async def query_content(self, content_type, first, last, filters,
                            order_by, order_sort, attrs,
                            seed_key=None, seed_value=None):
        """Handle a beonet:content query and return XML result."""
        # Refresh content if stale (>5 min)
        if time.monotonic() - self._last_content_fetch > 300:
            self._last_content_fetch = time.monotonic()
            self._background_tasks.spawn(
                self._fetch_content(), name="fetch_content_refresh")

        if content_type == 'track':
            return self._query_tracks(first, last, filters, order_by, order_sort, attrs, seed_key, seed_value)
        elif content_type == 'album-artist':
            return self._query_artists(first, last, filters, order_by, order_sort, attrs, seed_key, seed_value)
        elif content_type == 'album':
            return self._query_albums(first, last, filters, order_by, order_sort, attrs, seed_key, seed_value)
        else:
            return '<query_result revision="0" seed_offset="0" xmlns="beonet:content"></query_result>'

    def _calc_seed_offset(self, items, seed_key, seed_value, key_func):
        """Calculate the index of the first item >= seed_value.
        The Beo6 uses this to jump to a letter in the sorted list."""
        if not seed_key or not seed_value:
            return 0
        sv = seed_value.lower()
        for i, item in enumerate(items):
            if key_func(item).lower() >= sv:
                return i
        return len(items)

    def _query_tracks(self, first, last, filters, order_by, order_sort, attrs,
                      seed_key=None, seed_value=None):
        tracks = list(self._all_tracks)

        for attr, filt in filters.items():
            val = filt['value']
            opr = filt['opr']
            if attr == 'album.id':
                tracks = [t for t in tracks if
                          (str(t['album_id']) == val) == (opr == 'eq')]
            elif attr == 'id':
                tracks = [t for t in tracks if
                          (str(t['id']) == val) == (opr == 'eq')]
            elif attr == 'play-count' and opr == 'neq':
                tracks = [t for t in tracks if t['play_count'] != int(val)]

        # Sort
        if order_by == 'last-played-time':
            tracks.sort(key=lambda t: t['last_played'], reverse=(order_sort == 'desc'))
        elif order_by == 'added-time':
            tracks.sort(key=lambda t: t['added_time'], reverse=(order_sort == 'desc'))
        elif order_by == 'play-count':
            tracks.sort(key=lambda t: t['play_count'],
                        reverse=(order_sort == 'desc'))
        elif order_by == 'index':
            tracks.sort(key=lambda t: t['index'])
        elif order_by == 'name' or order_by == 'title':
            tracks.sort(key=lambda t: t['title'].lower(),
                        reverse=(order_sort == 'desc'))

        # Calculate seed offset
        seed_offset = 0
        if seed_key and seed_value:
            key_map = {'title': lambda t: t['title'], 'name': lambda t: t['title']}
            kf = key_map.get(seed_key, lambda t: t['title'])
            seed_offset = self._calc_seed_offset(tracks, seed_key, seed_value, kf)

        # Paginate
        if seed_offset:
            first += seed_offset
            last += seed_offset
        page = tracks[first:last + 1] if last >= first else []

        items_xml = ""
        for t in page:
            art_path = self._artwork_path(t.get('image', ''))
            art_path_large = self._artwork_path_large(t.get('image', ''))
            vals = {
                'title': t['title'],
                'id': str(t['id']),
                'album.title': t['album_title'],
                'album.extra-small-cover-url': art_path,
                'album.large-cover-url': art_path_large,
                'album.id': str(t['album_id']),
                'last-played-time': str(t['last_played']),
                'added-time': str(t['added_time']),
                'play-count': str(t['play_count']),
                'index': str(t['index']),
            }
            items_xml += '<item>'
            for a in attrs:
                items_xml += f'<a value="{_esc(vals.get(a, ""))}"></a>'
            items_xml += '</item>'

        # Build attr_column / seed_column headers
        cols_xml = ""
        for i, a in enumerate(attrs):
            cols_xml += f'<attr_column attr="{_esc(a)}" value="{i}"></attr_column>'
        if order_by:
            cols_xml += f'<seed_column seed="{_esc(order_by)}" value="{len(attrs)}"></seed_column>'

        return (
            f'<query_result revision="{self._content_revision}" '
            f'seed_offset="{seed_offset}" xmlns="beonet:content">'
            f'{cols_xml}{items_xml}</query_result>'
        )

    def _query_artists(self, first, last, filters, order_by, order_sort, attrs,
                       seed_key=None, seed_value=None):
        artists = sorted(self._artists.values(), key=lambda a: a['name'].lower())
        if order_sort == 'desc':
            artists.reverse()

        # Calculate seed offset
        seed_offset = 0
        if seed_key and seed_value:
            seed_offset = self._calc_seed_offset(artists, seed_key, seed_value,
                                                  lambda a: a['name'])

        if seed_offset:
            first += seed_offset
            last += seed_offset
        page = artists[first:last + 1] if last >= first else []

        items_xml = ""
        for a in page:
            vals = {'name': a['name'], 'id': str(a['id'])}
            items_xml += '<item>'
            for attr in attrs:
                items_xml += f'<a value="{_esc(vals.get(attr, ""))}"></a>'
            items_xml += '</item>'

        cols_xml = ""
        for i, a in enumerate(attrs):
            cols_xml += f'<attr_column attr="{_esc(a)}" value="{i}"></attr_column>'
        if order_by:
            cols_xml += f'<seed_column seed="{_esc(order_by)}" value="{len(attrs)}"></seed_column>'

        return (
            f'<query_result revision="{self._content_revision}" '
            f'seed_offset="{seed_offset}" xmlns="beonet:content">'
            f'{cols_xml}{items_xml}</query_result>'
        )

    def _query_albums(self, first, last, filters, order_by, order_sort, attrs,
                      seed_key=None, seed_value=None):
        albums = list(self._albums.values())

        # Filter by artist
        for attr, filt in filters.items():
            if attr == 'album-artist.id':
                val = filt['value']
                albums = [a for a in albums if str(a['artist_id']) == val]

        albums.sort(key=lambda a: a['title'].lower())
        if order_sort == 'desc':
            albums.reverse()

        # Calculate seed offset
        seed_offset = 0
        if seed_key and seed_value:
            seed_offset = self._calc_seed_offset(albums, seed_key, seed_value,
                                                  lambda a: a['title'])

        if seed_offset:
            first += seed_offset
            last += seed_offset
        page = albums[first:last + 1] if last >= first else []

        items_xml = ""
        for a in page:
            art_path = self._artwork_path(a.get('image', ''))
            art_path_large = self._artwork_path_large(a.get('image', ''))
            vals = {
                'title': a['title'],
                'id': str(a['id']),
                'album-artist.id': str(a['artist_id']),
                'extra-small-cover-url': art_path,
                'large-cover-url': art_path_large,
                'release-year': '2024',
            }
            items_xml += '<item>'
            for attr in attrs:
                items_xml += f'<a value="{_esc(vals.get(attr, ""))}"></a>'
            items_xml += '</item>'

        cols_xml = ""
        for i, a in enumerate(attrs):
            cols_xml += f'<attr_column attr="{_esc(a)}" value="{i}"></attr_column>'
        if order_by:
            cols_xml += f'<seed_column seed="{_esc(order_by)}" value="{len(attrs)}"></seed_column>'

        return (
            f'<query_result revision="{self._content_revision}" '
            f'seed_offset="{seed_offset}" xmlns="beonet:content">'
            f'{cols_xml}{items_xml}</query_result>'
        )

    def _artwork_path(self, url):
        """Convert a real artwork URL to a BM5-style path the Beo6 can fetch via port 8080."""
        if not url:
            return ""
        # Synthetic tracks store as "synth:<hash>" — hash already in _art_map
        if url.startswith('synth:'):
            h = url[6:]
            return f"E:\\Cache\\Covers\\Hdd\\{h[:2]}\\{h}_64.jpg"
        # data: URIs — extract base64 and store in _art_map
        if url.startswith('data:'):
            art_hash = hashlib.md5(url[:100].encode()).hexdigest()
            try:
                b64_data = url.split(',', 1)[1]
                self._art_map[art_hash] = f"base64:{b64_data}"
            except (IndexError, Exception):
                return ""
            return f"E:\\Cache\\Covers\\Hdd\\{art_hash[:2]}\\{art_hash}_64.jpg"
        # Use MD5 hash to match BM5 format: E:\Cache\Covers\Hdd\XX\<hash>_64.jpg
        h = hashlib.md5(url.encode()).hexdigest()
        subdir = h[:2]
        # Store mapping for reverse lookup in art handler
        self._art_map[h] = url
        return f"E:\\Cache\\Covers\\Hdd\\{subdir}\\{h}_64.jpg"

    def _artwork_path_large(self, url):
        """Like _artwork_path but returns _512.jpg for large-cover-url queries."""
        if not url:
            return ""
        if url.startswith('synth:'):
            h = url[6:]
            return f"E:\\Cache\\Covers\\Hdd\\{h[:2]}\\{h}_512.jpg"
        if url.startswith('data:'):
            art_hash = hashlib.md5(url[:100].encode()).hexdigest()
            try:
                b64_data = url.split(',', 1)[1]
                self._art_map[art_hash] = f"base64:{b64_data}"
            except (IndexError, Exception):
                return ""
            return f"E:\\Cache\\Covers\\Hdd\\{art_hash[:2]}\\{art_hash}_512.jpg"
        h = hashlib.md5(url.encode()).hexdigest()
        subdir = h[:2]
        self._art_map[h] = url
        return f"E:\\Cache\\Covers\\Hdd\\{subdir}\\{h}_512.jpg"

    def _queue_attr_value(self, track, attr):
        """Resolve a BeoNet queue attribute name to a track data value."""
        if attr == 'track.id':
            return str(track['id'])
        elif attr == 'track.title':
            return track.get('title', '')
        elif attr == 'track.album.title':
            return track.get('album_title', '')
        elif attr == 'track.album.album-artist.name':
            return track.get('artist', '') or 'Unknown'
        elif attr == 'track.album.large-cover-url':
            return self._artwork_path_large(track.get('image', ''))
        elif attr == 'track.album.extra-small-cover-url':
            return self._artwork_path(track.get('image', ''))
        elif attr == 'track.index':
            return str(track.get('index', 0))
        elif attr == 'from-mots':
            return 'true'
        else:
            log.debug("Unknown queue attr: %s", attr)
            return ''

    async def query_queue(self, queue_id, pos, first_offset, last_offset, requested_attrs=None):
        """Handle play queue query.

        Uses relative offsets as track IDs: current="0", next="1", prev="-1".
        content_id is always "0" so the Beo6 always finds the current track.
        When user clicks item with id=N, Beo6 sends skip offset=(N - 0) = N.
        skip_to_offset() resolves to router's current_index + N.
        """
        if requested_attrs is None:
            requested_attrs = []

        items_xml = ""
        now = self._now_playing

        # Fetch queue from router to get current position and tracks
        center = 0
        queue_tracks = {}  # relative_offset -> track data
        try:
            async with self._http.get(
                f"{ROUTER_URL}/router/queue?start=0&max_items=1",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                if resp.status == 200:
                    peek = await resp.json()
                    center = peek.get("current_index", 0)

            first_abs = center + first_offset
            last_abs = center + last_offset
            start = max(0, first_abs)
            count = last_abs - start + 1
            if count > 0:
                async with self._http.get(
                    f"{ROUTER_URL}/router/queue?start={start}&max_items={count}",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp2:
                    if resp2.status == 200:
                        data = await resp2.json()
                        for qt in data.get("tracks", []):
                            abs_idx = qt.get("index", 0)
                            rel = abs_idx - center
                            queue_tracks[rel] = qt
        except Exception as e:
            log.debug("Queue fetch failed: %s", e)

        # Use absolute router queue index as content_id for stable matching
        self.content_id = str(center)
        log.info("Queue query: center=%d, offsets=%d..%d, %d router tracks",
                 center, first_offset, last_offset, len(queue_tracks))

        # Build items for each requested relative position
        for rel_idx in range(first_offset, last_offset + 1):
            abs_idx = center + rel_idx
            if rel_idx == 0 and now:
                # Offset 0: currently playing track (from _now_playing metadata)
                track = {
                    "id": str(abs_idx),
                    "title": now.get("title", ""),
                    "artist": now.get("artist", ""),
                    "album_title": now.get("album_title", ""),
                    "image": now.get("image", ""),
                    "index": now.get("index", abs_idx + 1),
                }
            elif rel_idx in queue_tracks:
                qt = queue_tracks[rel_idx]
                track = {
                    "id": str(abs_idx),
                    "title": qt.get("title", ""),
                    "artist": qt.get("artist", ""),
                    "album_title": qt.get("album", ""),
                    "image": qt.get("artwork", ""),
                    "index": abs_idx + 1,
                }
            else:
                continue  # no data for this position

            vals = [f'<a value="{_esc(str(track["id"]))}"></a>']
            for attr in requested_attrs:
                vals.append(f'<a value="{_esc(self._queue_attr_value(track, attr))}"></a>')
            items_xml += f'<item>{"".join(vals)}</item>'

        # BM5 format: id column first, items, then remaining attr_columns
        cols_xml = ""
        for i, attr in enumerate(requested_attrs):
            cols_xml += f'<attr_column attr="{_esc(attr)}" value="{i + 1}"></attr_column>'

        return (
            f'<query_result revision="{self.pq_revision}" '
            f'seed_offset="0" xmlns="beonet:player">'
            f'<attr_column attr="id" value="0"></attr_column>'
            f'{items_xml}{cols_xml}</query_result>'
        )

    # -- Playback control --

    async def play_track(self, track_id):
        """Play a track by its content ID."""
        if track_id is None:
            return

        track = None
        for t in self._all_tracks:
            if str(t['id']) == str(track_id):
                track = t
                break

        if not track:
            log.warning("Track ID %s not found", track_id)
            return

        self.content_id = "0"  # always "0" — current track (relative offset scheme)
        self.pq_revision += 1
        self.queue_id = str(self.pq_revision)

        playlist_id = track.get('_playlist_id')
        if playlist_id:
            track_count = 0
            playlist_tracks = []
            for pl in self._playlists:
                if pl.get('id') == playlist_id:
                    playlist_tracks = pl.get('tracks', [])
                    track_count = len(playlist_tracks)
                    break
            track_index = track.get('_playlist_idx', 0)

            # Set now-playing immediately so Beo6 shows the right track
            if 0 <= track_index < len(playlist_tracks):
                pt = playlist_tracks[track_index]
                pl_image = next((pl.get('image', '') for pl in self._playlists
                                 if pl.get('id') == playlist_id), '')
                art_url = pt.get('image', pl_image)
                image_ref = ''
                if art_url:
                    if art_url.startswith('data:'):
                        art_hash = hashlib.md5(
                            f"np_{pt.get('name','')}_{pt.get('artist','')}".encode()
                        ).hexdigest()
                        try:
                            b64_data = art_url.split(',', 1)[1]
                            self._art_map[art_hash] = f"base64:{b64_data}"
                            image_ref = f"synth:{art_hash}"
                        except (IndexError, Exception):
                            pass
                    else:
                        image_ref = art_url
                self.title = pt.get('name', '')
                self.artist = pt.get('artist', '')
                self._now_playing = {
                    'id': self.content_id,
                    'title': self.title,
                    'artist': self.artist,
                    'album_title': track['album_title'],
                    'image': image_ref,
                    'index': 0,
                }

            try:
                async with self._http.post(
                    f"http://localhost:{self.source_port}/command",
                    json={"command": "play_playlist",
                          "playlist_id": playlist_id,
                          "track_index": track_index},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    log.info("Play playlist %s track %d -> %d",
                             playlist_id, track_index, resp.status)
            except Exception as e:
                log.error("Failed to play playlist: %s", e)
        else:
            uri = track.get('uri', '')
            if uri:
                try:
                    async with self._http.post(
                        f"http://localhost:{self.source_port}/command",
                        json={"command": "play_track", "uri": uri},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        log.info("Play track %s -> %d", uri, resp.status)
                except Exception as e:
                    log.error("Failed to play track: %s", e)

    async def skip_next(self):
        """Skip to next track."""
        try:
            async with self._http.post(
                f"{PLAYER_URL}/player/next",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                log.info("Skip next -> %d", resp.status)
        except Exception as e:
            log.error("Skip next failed: %s", e)

    async def skip_prev(self):
        """Skip to previous track."""
        try:
            async with self._http.post(
                f"{PLAYER_URL}/player/prev",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                log.info("Skip prev -> %d", resp.status)
        except Exception as e:
            log.error("Skip prev failed: %s", e)

    async def skip_to_offset(self, offset):
        """Skip by offset in the queue (positive=forward, negative=back, 0=replay)."""
        try:
            # Get current queue position from router
            async with self._http.get(
                f"{ROUTER_URL}/router/queue?start=0&max_items=1",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                if resp.status != 200:
                    log.warning("skip_to_offset: queue peek failed HTTP %d", resp.status)
                    return
                data = await resp.json()
                current = data.get("current_index", 0)

            target = current + offset
            if target < 0:
                target = 0
            log.info("Skip to offset %d: current=%d target=%d", offset, current, target)

            async with self._http.post(
                f"{ROUTER_URL}/router/queue/play",
                json={"position": target},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                log.info("Skip to queue position %d -> %d", target, resp.status)
        except Exception as e:
            log.error("skip_to_offset failed: %s", e)

    # -- Cover art server --

    async def _handle_art_request(self, request):
        """Serve cover art — proxy to real artwork URLs."""
        path = request.query.get('path', '')
        log.debug("Art request: path=%s", path[:100])
        if not path:
            return web.Response(status=404)

        # Extract hash from BM5-style path: E:\Cache\Covers\Hdd\XX\<hash>_64.jpg
        # or E:\Cache\Covers\Hdd\XX\<hash>_512.jpg (resized)
        url = None
        parts = path.replace('/', '\\').split('\\')
        if len(parts) >= 2:
            filename = parts[-1]  # e.g. "abc123_64.jpg"
            h = filename.split('_')[0] if '_' in filename else filename.split('.')[0]
            url = self._art_map.get(h)

        if not url:
            log.debug("Art hash not found: %s", path[:100])
            return web.Response(status=404)

        # BM5 sizes: _64.jpg = 64px, _81.jpg = 81px, _108.jpg = 108px, _512.jpg = 512px
        target_size = 512 if '_512' in path else 64
        width = request.query.get('width')
        height = request.query.get('height')
        if width and width.isdigit():
            target_size = int(width)
        elif height and height.isdigit():
            target_size = int(height)
        log.debug("Art fetch: target=%dpx path=%s", target_size, path[:80])

        try:
            # Resolve relative paths (e.g. CD artwork: assets/cd-cache/xxx.jpg)
            if url and not url.startswith(('http://', 'https://', 'base64:')):
                url = f"http://localhost:8000/{url}"

            # Handle base64-encoded artwork (from synthetic tracks)
            if url.startswith('base64:'):
                import base64
                data = base64.b64decode(url[7:])
            else:
                async with self._http.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        log.warning("Art upstream returned %d for %s", resp.status, url[:80])
                        return web.Response(status=resp.status)
                    data = await resp.read()

            # Resize to exact BM5 dimensions
            img = Image.open(io.BytesIO(data))
            img = img.convert('RGB')
            img = img.resize((target_size, target_size), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=85)
            out = buf.getvalue()
            log.info("Art served: %dx%d %d bytes (from %d) for %s",
                     target_size, target_size, len(out), len(data), url[:60])
            return web.Response(body=out, content_type='image/jpeg')
        except Exception as e:
            log.warning("Art fetch/resize failed for %s: %s", url[:80], e)
            return web.Response(status=502)


async def main():
    service = Beo6Service()
    await service.start()

    loop_monitor = LoopMonitor().start()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()

    await loop_monitor.stop()
    await service._background_tasks.cancel_all()
    if service._http:
        await service._http.close()


if __name__ == '__main__':
    asyncio.run(main())
