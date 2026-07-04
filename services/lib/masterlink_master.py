# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Master Link "master" role — BS5c is the audio master on the bus (node
# 0xC1 AUDIO_MASTER).  Replies to MASTER_PRESENT / AUDIO_BUS / GOTO_SOURCE
# from link devices, broadcasts clock, forwards Beo4 transport keys
# from link devices to beo-router, and asserts ML audio distribution
# whenever a link device has been seen and we power on local audio.
#
# Verified end-to-end against a BeoLab 2000 link speaker on Office BS5c
# (May 2026): pressing source buttons on the link engages distribute,
# audio reaches the BeoLab 2000, and Beo4 transport keys forward to the
# active source on the master.  PLAY on the BeoLab 2000 panel arrives
# as Beo4 keycode 0x53 over the IR/RF channel (USB msg type 0x02), not
# as an ML telegram — mapped in masterlink.py:process_beo4_keycode.
#
# Cold-idle exception: the BL2000 panel's PLAY button only emits Beo4
# 0x53 once an ML session has been engaged on the master.  In a fully
# idle state — no GOTO_SOURCE has happened yet, no source playing —
# pressing PLAY produces nothing on the bus.  We work around this with
# a wake-on-link-activity heuristic (_maybe_wake_on_link_activity):
# the first ML telegram from a link node after a >60 s quiet period
# triggers a synthetic ``go`` toward the router, which routes to "start
# default source" via its existing 1b2/1b3 fallbacks.  Skipped when the
# router reports something is already playing, so a BL2000 power-on
# does not pause music in another room.
#
# Boilerplate telegram shapes are derived from libpc2 (GPL-3.0) by
# Tore Sinding Bekkedal — see https://github.com/toresbe/libpc2,
# masterlink/telegram.cpp and masterlink/masterlink.cpp.  GPL-3.0-or-later
# compatible; the byte sequences themselves are protocol facts.

import asyncio
import logging
import time

import aiohttp

from lib.endpoints import ROUTER_STATUS
from lib.masterlink_common import ML_BEO4_TRANSPORT_ACTIONS, forward_to_router

logger = logging.getLogger('beo-masterlink')

# Source ID byte (in GOTO_SOURCE telegrams) → router action name.  When
# a link device sends GOTO_SOURCE, we activate the matching local source
# by forwarding this action to beo-router; its config-driven source_buttons
# map picks the right service.  Unmapped IDs still get a protocol reply
# (so the link doesn't hang) but no source is activated.
ML_SOURCE_TO_ACTION = {
    0x0B: "tv",
    0x15: "vmem",
    0x16: "dvd",
    0x1F: "dtv",
    0x29: "dvd",
    0x33: "v.aux",
    0x3E: "v.aux2",
    0x47: "pc",
    0x6F: "radio",
    0x79: "amem",
    0x7A: "amem",
    0x8D: "cd",
    0x97: "a.aux",
    0xA1: "n.radio",
}

# Display labels for source-id bytes.  Used only for log messages.
ML_SOURCE_LABELS = {
    0x0B: "TV",  0x15: "V_MEM", 0x16: "DVD_2", 0x1F: "SAT",   0x29: "DVD",
    0x33: "DTV_2", 0x3E: "V_AUX2", 0x47: "PC", 0x6F: "RADIO", 0x79: "A_MEM",
    0x7A: "A_MEM2", 0x8D: "CD", 0x97: "A_AUX", 0xA1: "N_RADIO",
}

# How recently a link device must have pinged us to count as "present".
# audio_on() consults wants_distribute() to decide whether to engage the
# ML audio bus; if a link device has gone quiet for longer than this we
# assume it's gone and skip the (harmless) distribute assertion.
LINK_PRESENCE_WINDOW = 300.0  # seconds

# Cadence for clock broadcasts to the bus.  libpc2 doesn't commit to a
# value; 60 s matches what real B&O masters do in casual sniffs.
CLOCK_BROADCAST_INTERVAL = 60.0

# Wake-on-link-activity tuning.  The BL2000 panel's PLAY button only
# emits Beo4 0x53 once an ML session is engaged, so PLAY-from-cold-idle
# is silently dropped on the bus.  We work around it by treating the
# first ML telegram from a link node after a quiet period as a panel-
# wake event and forwarding ``go`` to the router (which the router
# already handles correctly: idle → activate default, paused → resume,
# playing → pause).
#
# QUIET threshold: how long a link node must have been silent before
# its next telegram counts as "user just touched the panel".  Periodic
# heartbeat polls keep the timestamp fresh, so they don't trigger.
# DEDUP window: don't re-fire wake within this many seconds of a prior
# fire, even if the link goes quiet and back again.
LINK_WAKE_QUIET_THRESHOLD = 60.0
LINK_WAKE_DEDUP_WINDOW = 30.0


class MasterRole:
    """BS5c-as-audio-master — handles MASTER_PRESENT / AUDIO_BUS / GOTO_SOURCE
    + BEO4_KEY forwarding, broadcasts clock, tracks link-device presence
    so audio_on() can engage ML distribution automatically."""

    def __init__(self, pc2):
        self.pc2 = pc2
        self._last_link_seen = 0.0  # monotonic; 0 = never
        # Last source we asserted via GOTO_SOURCE.  REQUEST_LOCAL_SOURCE
        # replies name this so a link device knows what's playing without
        # having to wait for a fresh STATUS_INFO broadcast.
        self._active_source_byte = None
        # Per-link-node timestamp of last incoming telegram.  Used by
        # _maybe_wake_on_link_activity to detect "first telegram after a
        # quiet period" as a panel-wake trigger — the BL2000's panel
        # PLAY button doesn't reach the bus until a session is engaged,
        # so we use the wake-up signature itself as a proxy.
        self._link_telegram_at: dict[int, float] = {}
        self._last_wake_fired = 0.0  # monotonic; 0 = never

    # ── lifecycle ─────────────────────────────────────────────────────

    def start(self, loop):
        loop.create_task(self._clock_broadcast_loop())
        logger.info("ML master role: clock broadcast loop started")

    # ── helpers consulted by PC2Device ────────────────────────────────

    def wants_distribute(self):
        """True when audio_on() should assert distribute=True so a link
        speaker hears the bus.  Gated on ever having seen a link device
        ping us within ``LINK_PRESENCE_WINDOW`` — keeps standalone
        deployments from flipping a routing bit nobody listens to."""
        if not self._last_link_seen:
            return False
        return (time.monotonic() - self._last_link_seen) <= LINK_PRESENCE_WINDOW

    # ── public dispatch entrypoint ────────────────────────────────────

    def handle_telegram(self, ttype, ptype, src_node, dest_node,
                        src_src, payload):
        """Called from PC2Device._dispatch_ml when role == 'master'."""

        # STANDBY (0x10) from the link side — e.g. Beo4 ALL-off pressed in
        # a link room, or a link device commanding standby.  Follow it
        # locally.  Handled BEFORE wake detection so a standby arriving
        # after a quiet period can't be misread as a panel wake (which
        # would start music instead of stopping it).
        if ptype == 0x10:
            if src_node not in (0x80, 0x81, 0x83):
                # Still counts as bus activity for the wake tracker.
                self._link_telegram_at[src_node] = time.monotonic()
            logger.info("ML STANDBY from %s — going to standby",
                        self.pc2.node_label(src_node))
            self._forward_router(src_node, "off", label="ML standby")
            return

        # Panel-wake detection.  Runs before per-payload handlers so a
        # quiet→active transition fires regardless of which telegram
        # type carries the wake (BL2000 has been observed leading with
        # MASTER_PRESENT, AUDIO_BUS, or REQUEST_LOCAL_SOURCE depending
        # on its internal state).  No-op for broadcast addresses.
        self._maybe_wake_on_link_activity(src_node)

        # REQUEST / MASTER_PRESENT — "is there an audio master here?"
        if ttype == 0x0B and ptype == 0x04:
            logger.info("ML master: MASTER_PRESENT request from 0x%02X",
                        src_node)
            self._mark_link_seen(src_node)
            self._reply_master_present(src_node)
            return

        # AUDIO_SETUP-style link/video device ping (payload_type 0x04 with a
        # specific 3-byte payload).  From libpc2 masterlink.cpp commented
        # case(0x04): payload[0]=0x08 (link device) or 0x02 (video device).
        # Reply is the same MASTER_PRESENT status, regardless of incoming
        # telegram_type — link rooms send these with non-REQUEST ttype.
        if ptype == 0x04 and len(payload) == 3 and payload[1] == 0x01 \
                and payload[2] == 0x00 and payload[0] in (0x02, 0x08):
            kind = "link device" if payload[0] == 0x08 else "video device"
            logger.info("ML master: %s ping from 0x%02X — replying", kind,
                        src_node)
            self._mark_link_seen(src_node)
            self._reply_master_present(src_node)
            return

        # AUDIO_BUS request (payload_type 0x08, empty payload, pver=1).  From
        # libpc2 masterlink.cpp commented case(0x08): "Not sure what this
        # means but link room products will sometimes need this reply".
        if ptype == 0x08 and len(payload) == 0:
            logger.info("ML master: AUDIO_BUS request from 0x%02X — replying",
                        src_node)
            self._mark_link_seen(src_node)
            self._reply_audio_bus(src_node)
            return

        # REQUEST / GOTO_SOURCE — link device wants us to play a source.
        if ttype == 0x0B and ptype == 0x45:
            logger.info("ML master: GOTO_SOURCE from 0x%02X payload=%s",
                        src_node, " ".join(f"{b:02X}" for b in payload))
            self._mark_link_seen(src_node)
            self._handle_goto_source(src_node, payload)
            return

        # COMMAND / BEO4_KEY — link device forwarding a Beo4 transport key.
        # Payload: [source_byte, keycode].
        if ttype == 0x0A and ptype == 0x0D:
            self._mark_link_seen(src_node)
            self._handle_beo4_key(src_node, payload)
            return

        # COMMAND / RELEASE — link device telling us a key was released.
        # We don't track repeats per-device, so this is a no-op.  Logged
        # at INFO while we're still figuring out which buttons emit which
        # telegrams; can drop to debug once the protocol is fully mapped.
        if ttype == 0x0A and ptype == 0x11:
            logger.info("ML master: RELEASE from 0x%02X (no-op) payload=%s",
                        src_node,
                        " ".join(f"{b:02X}" for b in payload[:8]))
            return

        # REQUEST / REQUEST_LOCAL_SOURCE — link device asking what source
        # we're providing.  Replied to with a STATUS_INFO addressed back
        # to the requester (when we have an active source).  Earlier
        # hypothesis that an accelerated poll cadence here meant PLAY
        # turned out wrong — PLAY actually arrives separately as the
        # Beo4 keycode 0x53 over the IR/RF channel (USB msg type 0x02).
        if ttype == 0x0B and ptype == 0x30:
            self._mark_link_seen(src_node)
            self._handle_request_local_source(src_node)
            return

        logger.info("ML master: NO HANDLER t=0x%02X p=0x%02X from 0x%02X "
                    "payload=%s — link device may hang",
                    ttype, ptype, src_node,
                    " ".join(f"{b:02X}" for b in payload[:16]))

    # ── presence tracking ─────────────────────────────────────────────

    def _mark_link_seen(self, src_node):
        if src_node in (0x80, 0x81, 0x83):
            return  # broadcast addresses, not real devices
        self._last_link_seen = time.monotonic()

    # ── wake-on-link-activity ─────────────────────────────────────────

    def _maybe_wake_on_link_activity(self, src_node):
        """First telegram from a link node after a quiet period → forward
        ``go`` to the router as a synthetic PLAY.

        The BL2000 panel's PLAY button only emits Beo4 0x53 once an ML
        session is engaged, so PLAY-from-cold-idle disappears.  Touching
        the panel (or coming out of standby) does cause the BL2000 to
        send ML telegrams though, and we use that quiet→active edge as a
        substitute trigger.

        ``go`` is forwarded rather than ``activate``/``play`` because
        the router already does the right thing for every state on a
        ``go`` press: idle → activate default, paused → resume, playing
        → pause.  That matches the user-facing "play = start if idle,
        pause if playing" semantics they expect from a panel PLAY.
        """
        if src_node in (0x80, 0x81, 0x83):
            return  # broadcast addresses, not real devices
        now = time.monotonic()
        prev = self._link_telegram_at.get(src_node, 0.0)
        was_quiet = (now - prev) > LINK_WAKE_QUIET_THRESHOLD
        self._link_telegram_at[src_node] = now
        if not was_quiet:
            return
        if (now - self._last_wake_fired) < LINK_WAKE_DEDUP_WINDOW:
            return
        if not (self.pc2.session and self.pc2.loop):
            logger.debug("Link wake: session/loop not ready, dropping")
            return
        self._last_wake_fired = now
        asyncio.run_coroutine_threadsafe(
            self._wake_if_router_idle(src_node), self.pc2.loop)

    async def _wake_if_router_idle(self, src_node):
        """Async helper for _maybe_wake_on_link_activity.

        Queries the router's status and only forwards ``go`` when nothing
        is currently playing locally — otherwise a link wake would
        clobber audio in a different room (e.g., user walks into the
        BL2000 room and powers the speaker on while music plays at the
        master).  A paused source still gets the wake (router will
        resume it), and a fully idle master gets the default-source
        activation path.
        """
        try:
            async with self.pc2.session.get(
                ROUTER_STATUS,
                timeout=aiohttp.ClientTimeout(total=0.5),
            ) as resp:
                if resp.status != 200:
                    logger.debug("Link wake: router status HTTP %d, skipping",
                                 resp.status)
                    return
                data = await resp.json()
        except Exception as e:
            logger.debug("Link wake: router unreachable (%s), skipping", e)
            return
        active_id = data.get("active_source")
        if active_id:
            sources = data.get("sources", {})
            state = sources.get(active_id, {}).get("state")
            if state == "playing":
                logger.info("Link wake: 0x%02X panel touch, but %s is "
                            "already playing — skipping",
                            src_node, active_id)
                return
        link_name = self.pc2.node_label(src_node)
        logger.info("Link wake: 0x%02X panel touch, router idle/paused — "
                    "forwarding 'go'", src_node)
        await forward_to_router(self.pc2.session, source="masterlink",
                                action="go", device_type="Audio",
                                link=link_name)

    # ── master replies ────────────────────────────────────────────────

    def _reply_master_present(self, requesting_node):
        """Reply payload {0x01, 0x01, 0x01} pver=4 from libpc2
        telegram.cpp DecodedTelegram::MasterPresent::reply_from_request()."""
        self.pc2.send_ml_telegram(
            dest_node=requesting_node,
            src_node=self.pc2.OUR_NODE_ID,
            telegram_type=0x14,        # STATUS
            payload_type=0x04,         # MASTER_PRESENT
            payload_version=4,
            payload=[0x01, 0x01, 0x01],
        )

    def _reply_audio_bus(self, requesting_node):
        """Empty payload pver=4 from libpc2 masterlink.cpp case(0x08)."""
        self.pc2.send_ml_telegram(
            dest_node=requesting_node,
            src_node=self.pc2.OUR_NODE_ID,
            telegram_type=0x14,        # STATUS
            payload_type=0x08,         # AUDIO_BUS
            payload_version=4,
            payload=[],
        )

    # ── GOTO_SOURCE handling ──────────────────────────────────────────

    def _handle_goto_source(self, src_node, payload):
        """Respond to a link-device source request and start the source
        locally.  Sends STATUS_INFO + TRACK_INFO replies, asserts
        distribute routing on PowerLink hosts, and forwards a synthetic IR
        event to beo-router so the local source service activates.

        Payload byte [1] is the requested source ID per libpc2 GotoSource."""
        if len(payload) < 2:
            logger.warning("GOTO_SOURCE payload too short (%d bytes): %s",
                           len(payload),
                           " ".join(f"{b:02X}" for b in payload))
            return
        source_id = payload[1]
        source_name = ML_SOURCE_LABELS.get(source_id, f"0x{source_id:02X}")
        logger.info("GOTO_SOURCE: src_node=0x%02X source_id=0x%02X (%s)",
                    src_node, source_id, source_name)
        self._engage_session(src_node, source_id)

        # Forward to beo-router as a synthetic source-button press.  The
        # router's source-button path forwards an ``activate`` action to
        # the source service, whose ``activate_playback`` starts/resumes
        # playback (every source implements this — radio plays current
        # station, spotify resumes last track, etc.).  Pressing a source
        # button on a link that's already playing locally is a no-op at
        # the router (step 2 wake-screen).
        action = ML_SOURCE_TO_ACTION.get(source_id)
        if not action:
            logger.warning("GOTO_SOURCE 0x%02X (%s): no action mapping — "
                           "session engaged but no local source activated",
                           source_id, source_name)
            return
        self._forward_router(src_node, action, label=f"GOTO_SOURCE {action}")

    def _engage_session(self, src_node, source_id):
        """Engage an ML session for ``source_id`` toward ``src_node``.

        Three steps:
          1. STATUS_INFO broadcast to ALL_LINK_DEVICES (0x83).  31-byte
             payload scaffold from libpc2 telegram.cpp StatusInfo.  Most
             fields are "known unknowns" — B&O's status struct that link
             rooms inspect for source kind, track position, etc.
          2. TRACK_INFO addressed to the requester (libpc2 TrackInfo).
          3. ``set_routing(local=True, distribute=True)`` so audio rides
             the ML bus.  Only effective on PowerLink hosts; on Sonos /
             BlueSound / ESPHome BeoLab 5, audio bypasses the PC2 so this
             step is a no-op (telegrams still go out so the link doesn't
             hang waiting for them).

        Cached as ``_active_source_byte`` so REQUEST_LOCAL_SOURCE replies
        and PLAY-burst re-engages can find it.
        """
        self._active_source_byte = source_id
        status_payload = [
            source_id, 0x01, 0x00, 0x00, 0x1F, 0xBE, 0x01, 0x00,
            0x00, 0x00, 0xFF, 0x02, 0x01, 0x00, 0x03, 0x01,
            0x01, 0x01, 0x03, 0x00, 0x02, 0x00, 0x00, 0x00,
            0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00,
        ]
        self.pc2.send_ml_telegram(
            dest_node=0x83,
            src_node=self.pc2.OUR_NODE_ID,
            telegram_type=0x14,
            payload_type=0x87,
            payload_version=4,
            payload=status_payload,
        )
        self.pc2.send_ml_telegram(
            dest_node=src_node,
            src_node=self.pc2.OUR_NODE_ID,
            telegram_type=0x14,
            payload_type=0x44,
            payload_version=5,
            payload=[0x02, source_id, 0x00, 0x02, 0x01, 0x00, 0x00, 0x00],
        )
        if self.pc2.is_powerlink_device():
            try:
                self.pc2.set_routing(local=True, distribute=True)
            except Exception as e:
                logger.warning("set_routing(distribute) failed: %s", e,
                               exc_info=True)

    def _forward_router(self, src_node, action, *, label):
        """Send a synthetic IR-style action to beo-router."""
        if not (self.pc2.session and self.pc2.loop):
            logger.warning("%s: router session not ready, dropping", label)
            return
        link_name = self.pc2.node_label(src_node)
        logger.info("%s -> router action=%r from %s", label, action,
                    link_name)
        asyncio.run_coroutine_threadsafe(
            forward_to_router(self.pc2.session, source="masterlink",
                              action=action, device_type="Audio",
                              link=link_name),
            self.pc2.loop,
        )

    # ── REQUEST_LOCAL_SOURCE handling ─────────────────────────────────

    def _handle_request_local_source(self, src_node):
        """Reply to a link device's "what's playing?" poll.

        BeoLab 2000 polls this every ~17 s on idle and adds extra polls
        around state changes.  When we have a cached active source we
        reply with STATUS_INFO so the link can render the current source
        on its panel; with nothing cached we just log and skip."""
        if self._active_source_byte is None:
            logger.info("ML master: REQUEST_LOCAL_SOURCE from 0x%02X — no "
                        "active source cached, skipping STATUS_INFO reply",
                        src_node)
            return
        source_id = self._active_source_byte
        logger.info("ML master: REQUEST_LOCAL_SOURCE from 0x%02X — "
                    "replying with STATUS_INFO source=0x%02X (%s)",
                    src_node, source_id,
                    ML_SOURCE_LABELS.get(source_id, f"0x{source_id:02X}"))
        status_payload = [
            source_id, 0x01, 0x00, 0x00, 0x1F, 0xBE, 0x01, 0x00,
            0x00, 0x00, 0xFF, 0x02, 0x01, 0x00, 0x03, 0x01,
            0x01, 0x01, 0x03, 0x00, 0x02, 0x00, 0x00, 0x00,
            0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00,
        ]
        self.pc2.send_ml_telegram(
            dest_node=src_node,
            src_node=self.pc2.OUR_NODE_ID,
            telegram_type=0x14,        # STATUS
            payload_type=0x87,         # STATUS_INFO
            payload_version=4,
            payload=status_payload,
        )

    # ── BEO4_KEY forwarding ───────────────────────────────────────────

    def _handle_beo4_key(self, src_node, payload):
        """Forward a Beo4 transport key from a link device to the router.

        Payload layout: ``[source_byte, keycode]``.  Source byte echoes
        the most recent GOTO_SOURCE; we ignore it because the router
        already knows which source is active.  Unmapped keycodes log so
        we can extend ``ML_BEO4_TRANSPORT_ACTIONS`` as new buttons appear."""
        if len(payload) < 2:
            logger.warning("BEO4_KEY payload too short from 0x%02X: %s",
                           src_node, " ".join(f"{b:02X}" for b in payload))
            return
        keycode = payload[1]
        action = ML_BEO4_TRANSPORT_ACTIONS.get(keycode)
        if not action:
            logger.info("BEO4_KEY from 0x%02X: unmapped keycode=0x%02X "
                        "payload=%s — add to ML_BEO4_TRANSPORT_ACTIONS to "
                        "route it", src_node, keycode,
                        " ".join(f"{b:02X}" for b in payload))
            return
        self._forward_router(src_node, action,
                             label=f"BEO4_KEY 0x{keycode:02X}")

    # ── clock broadcast ───────────────────────────────────────────────

    async def _clock_broadcast_loop(self):
        """A real audio master broadcasts the time periodically so link
        device displays stay updated.  Cadence is our choice."""
        while self.pc2.running:
            try:
                if self.pc2.connected:
                    self._broadcast_clock_once()
                    logger.debug("ML clock broadcast tick")
            except Exception as e:
                logger.warning("Clock broadcast failed: %s", e, exc_info=True)
            await asyncio.sleep(CLOCK_BROADCAST_INTERVAL)

    def _broadcast_clock_once(self):
        """Payload + BCD encoding from libpc2 masterlink.cpp
        PC2Beolink::broadcast_timestamp()."""
        t = time.localtime()

        def bcd(n: int) -> int:
            return ((n // 10) << 4) | (n % 10)

        payload = [
            0x0A, 0x00, 0x03,
            bcd(t.tm_hour), bcd(t.tm_min), bcd(t.tm_sec),
            0x00,
            bcd(t.tm_mday), bcd(t.tm_mon), bcd(t.tm_year % 100),
            0x02,
        ]
        self.pc2.send_ml_telegram(
            dest_node=0x80,            # ALL
            src_node=self.pc2.OUR_NODE_ID,
            telegram_type=0x14,        # STATUS
            payload_type=0x40,         # CLOCK
            payload_version=11,
            payload=payload,
        )
