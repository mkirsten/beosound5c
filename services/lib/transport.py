"""
Transport abstraction for BeoSound 5c <-> Home Assistant communication.

Supports webhook (HTTP POST), MQTT, or both transports, configurable
via TRANSPORT_MODE environment variable.

Usage:
    transport = Transport()
    await transport.start()
    await transport.send_event({"action": "go", "device_type": "Audio", ...})
    transport.set_command_handler(my_callback)
    await transport.stop()
"""

import asyncio
import json
import os
import re
import logging

import aiohttp

from .config import cfg

logger = logging.getLogger(__name__)

# Topic structure: beosound5c/{device_slug}/out|in|status
TOPIC_PREFIX = "beosound5c"


def _device_slug(name: str) -> str:
    """Convert device name to MQTT-safe slug: 'Living Room' -> 'living_room'.

    Strips characters that are illegal in MQTT topic segments (/, #, +)
    and replaces non-alphanumeric chars with underscores.
    """
    slug = name.lower().strip()
    # Replace spaces and any non-alphanumeric/underscore chars with underscore
    slug = re.sub(r"[^a-z0-9_]", "_", slug)
    # Collapse multiple underscores
    slug = re.sub(r"_+", "_", slug)
    # Strip leading/trailing underscores
    slug = slug.strip("_")
    return slug or "default"


class Transport:
    """Unified transport for sending events to HA and receiving commands."""

    def __init__(self):
        # Config from JSON config + env vars for secrets
        self.mode = cfg("transport", "mode", default="webhook").lower()  # webhook | mqtt | both
        self.webhook_url = cfg("home_assistant", "webhook_url",
                               default="http://homeassistant.local:8123/api/webhook/beosound5c")
        self.device_name = cfg("device", default="BeoSound5c")
        self.device_slug = _device_slug(self.device_name)

        # MQTT config (broker from JSON, credentials from env secrets)
        self.mqtt_broker = cfg("transport", "mqtt_broker", default="homeassistant.local")
        self.mqtt_port = int(cfg("transport", "mqtt_port", default=1883))
        self.mqtt_user = os.getenv("MQTT_USER", "")
        self.mqtt_password = os.getenv("MQTT_PASSWORD", "")

        # Topics (device-perspective: out = device sends, in = device receives)
        self.topic_out = f"{TOPIC_PREFIX}/{self.device_slug}/out"
        self.topic_in = f"{TOPIC_PREFIX}/{self.device_slug}/in"
        self.topic_status = f"{TOPIC_PREFIX}/{self.device_slug}/status"

        # Internal state
        self._session: aiohttp.ClientSession | None = None
        self._mqtt_client = None
        self._mqtt_task: asyncio.Task | None = None
        self._command_handler = None
        self._extra_subscriptions: list[tuple[str, callable]] = []
        self._running = False

        # Webhook circuit breaker. On a device without Home Assistant the
        # default webhook_url never resolves; without this, every HA-bound
        # event blocks its caller for the full 2s timeout and logs a warning.
        # After a few consecutive failures we suppress sends (one probe per
        # minute) until HA answers again.
        self._webhook_failures = 0
        self._webhook_suppressed_since: float | None = None
        self._webhook_last_probe = 0.0

    def add_subscription(self, topic: str, handler):
        """Add an extra MQTT topic subscription with its own handler."""
        self._extra_subscriptions.append((topic, handler))

    @property
    def _use_webhook(self) -> bool:
        return self.mode in ("webhook", "both")

    @property
    def _use_mqtt(self) -> bool:
        return self.mode in ("mqtt", "both")

    def set_command_handler(self, callback):
        """Register async callback for incoming MQTT commands.

        Callback signature: async def handler(data: dict) -> None
        """
        self._command_handler = callback

    async def start(self):
        """Initialize transports."""
        self._running = True

        if self._use_webhook:
            connector = aiohttp.TCPConnector(
                limit=10,
                ttl_dns_cache=300,
                keepalive_timeout=60,
                force_close=False,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=2.0),
                headers={"User-Agent": "BeoSound5c-Transport/1.0"},
            )
            logger.info("Webhook transport ready -> %s", self.webhook_url)

        if self._use_mqtt:
            self._mqtt_task = asyncio.create_task(self._mqtt_loop())
            logger.info("MQTT transport starting -> %s:%d", self.mqtt_broker, self.mqtt_port)

    async def stop(self):
        """Clean shutdown of all transports."""
        self._running = False

        if self._mqtt_task:
            self._mqtt_task.cancel()
            try:
                await self._mqtt_task
            except asyncio.CancelledError:
                pass
            self._mqtt_task = None

        if self._session:
            await self._session.close()
            self._session = None

        logger.info("Transport stopped")

    async def send_event(self, payload: dict):
        """Send an event to Home Assistant via configured transport(s).

        Runs webhook and MQTT sends in parallel when mode is 'both'.
        """
        tasks = []
        if self._use_webhook:
            tasks.append(self._send_webhook(payload))
        if self._use_mqtt:
            tasks.append(self._send_mqtt(payload))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.error("Transport send error: %s", r)

    # --- Webhook transport ---------------------------------------------------

    _WEBHOOK_TRIP_AFTER = 3     # consecutive failures before suppressing
    _WEBHOOK_PROBE_INTERVAL = 60.0  # seconds between probes while suppressed

    async def _send_webhook(self, payload: dict) -> bool:
        if not self._session:
            logger.warning("Webhook session not initialized")
            return False

        # While the circuit is open (HA unreachable), skip the send so the
        # caller isn't stalled for the 2s timeout on every event — but still
        # probe once a minute so HA coming online is picked up automatically.
        now = asyncio.get_event_loop().time()
        if self._webhook_suppressed_since is not None:
            if now - self._webhook_last_probe < self._WEBHOOK_PROBE_INTERVAL:
                return False
            self._webhook_last_probe = now

        try:
            async with self._session.post(
                self.webhook_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=2.0),
                raise_for_status=True,
            ) as resp:
                logger.debug("Webhook sent: %s (HTTP %d)", payload.get("action"), resp.status)
                if self._webhook_suppressed_since is not None:
                    logger.info("Webhook reachable again — resuming sends")
                    self._webhook_suppressed_since = None
                self._webhook_failures = 0
                return True
        except asyncio.TimeoutError:
            self._webhook_note_failure(now, "Webhook timeout for %s" % payload.get("action"))
        except aiohttp.ClientError as e:
            self._webhook_note_failure(now, "Webhook error: %s" % e)
        except Exception as e:
            logger.error("Webhook unexpected error: %s", e)
        return False

    def _webhook_note_failure(self, now: float, msg: str):
        self._webhook_failures += 1
        if self._webhook_suppressed_since is not None:
            return  # already suppressed — probes fail quietly
        if self._webhook_failures >= self._WEBHOOK_TRIP_AFTER:
            self._webhook_suppressed_since = now
            self._webhook_last_probe = now
            logger.warning(
                "%s — webhook unreachable %d times in a row, suppressing "
                "sends (will probe every %ds; is Home Assistant configured?)",
                msg, self._webhook_failures, int(self._WEBHOOK_PROBE_INTERVAL))
        else:
            logger.warning(msg)

    # --- MQTT transport -------------------------------------------------------

    async def _mqtt_loop(self):
        """Connect to MQTT broker with auto-reconnect and exponential backoff."""
        try:
            import aiomqtt
        except ImportError:
            logger.error("aiomqtt not installed -- MQTT transport disabled. "
                         "Install with: pip install aiomqtt")
            return

        backoff = 1  # seconds
        max_backoff = 30

        while self._running:
            try:
                will = aiomqtt.Will(
                    topic=self.topic_status,
                    payload=json.dumps({"status": "offline"}),
                    qos=1,
                    retain=True,
                )

                async with aiomqtt.Client(
                    hostname=self.mqtt_broker,
                    port=self.mqtt_port,
                    username=self.mqtt_user or None,
                    password=self.mqtt_password or None,
                    will=will,
                ) as client:
                    self._mqtt_client = client
                    backoff = 1  # reset on successful connect

                    # Publish online status (retained)
                    await client.publish(
                        self.topic_status,
                        json.dumps({"status": "online"}),
                        qos=1,
                        retain=True,
                    )
                    logger.info("MQTT connected to %s:%d", self.mqtt_broker, self.mqtt_port)

                    # Subscribe to commands
                    await client.subscribe(self.topic_in)
                    logger.info("MQTT subscribed to %s", self.topic_in)

                    # Subscribe to extra topics (e.g. lydbro-one)
                    for extra_topic, _ in self._extra_subscriptions:
                        await client.subscribe(extra_topic)
                        logger.info("MQTT subscribed to %s (extra)", extra_topic)

                    async for message in client.messages:
                        try:
                            data = json.loads(message.payload.decode())
                        except json.JSONDecodeError:
                            logger.warning("MQTT invalid JSON: %s", message.payload)
                            continue

                        # Check extra subscriptions first
                        handled = False
                        for extra_topic, handler in self._extra_subscriptions:
                            if message.topic.matches(extra_topic):
                                try:
                                    await handler(data)
                                except Exception as e:
                                    logger.error("MQTT extra handler error (%s): %s",
                                                 extra_topic, e)
                                handled = True
                                break

                        if not handled and message.topic.matches(self.topic_in):
                            try:
                                logger.info("MQTT command received: %s", data)
                                if self._command_handler:
                                    await self._command_handler(data)
                            except Exception as e:
                                logger.error("MQTT command handler error: %s", e)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._mqtt_client = None
                logger.warning("MQTT connection lost (%s), reconnecting in %ds", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

        self._mqtt_client = None

    async def _send_mqtt(self, payload: dict) -> bool:
        if not self._mqtt_client:
            logger.warning("MQTT not connected, dropping event: %s", payload.get("action"))
            return False

        try:
            await self._mqtt_client.publish(
                self.topic_out,
                json.dumps(payload),
                qos=0,
            )
            logger.debug("MQTT published: %s", payload.get("action"))
            return True
        except Exception as e:
            logger.warning("MQTT publish error: %s", e)
            return False
