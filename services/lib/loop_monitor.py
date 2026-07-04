"""Event-loop lag monitor.

Periodically schedules a zero-delay callback and measures how long it
took to actually run.  If the delay exceeds the configured threshold,
something on the loop ran synchronously for that long — almost always
a blocking sync call (subprocess.run, requests, big file read).

This is the complement to the per-handler latency middleware
(lib/correlation.py): the middleware catches slow *requests*, but a
blocking call inside a background task won't hit a handler.  The loop
monitor catches that case because every slow section eventually shows
up as a scheduling lag on the next tick.

Usage in a service::

    from lib.loop_monitor import LoopMonitor
    monitor = LoopMonitor().start()   # fire and forget
    # ... later, during shutdown ...
    await monitor.stop()

Usage in tests::

    async with LoopMonitor(warn_ms=0) as mon:
        await something()
    assert mon.max_lag_ms < 50
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

log = logging.getLogger("beo.loop")


class LoopMonitor:
    """Sample the event loop and emit a warning when it stalls.

    Parameters
    ----------
    interval_ms:
        How often to sample.  Defaults to 100 ms — short enough to
        catch sub-second stalls, long enough not to spam the loop.
    warn_ms:
        Threshold for logging a warning.  Defaults to 300 ms (the
        asyncio ``debug`` default).  Set to 0 in tests to record every
        sample into ``max_lag_ms``.
    logger_name:
        Named logger to emit warnings to.  Defaults to ``beo.loop``.
    """

    def __init__(
        self,
        interval_ms: int = 100,
        warn_ms: int = 300,
        logger_name: str = "beo.loop",
    ):
        self._interval = interval_ms / 1000
        self._warn = warn_ms / 1000
        self._logger = logging.getLogger(logger_name)
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self.samples: int = 0
        self.stalls: int = 0
        self.max_lag_ms: float = 0.0

    def start(self) -> "LoopMonitor":
        """Start sampling.  Must be called inside a running loop."""
        if self._task is not None:
            return self
        self._stop.clear()
        loop = asyncio.get_running_loop()
        # Opt-in: BEO_LOOP_DEBUG=1 turns on asyncio debug mode so the
        # loop logs the offending callback's traceback whenever any
        # callback runs longer than `slow_callback_duration`.  Off by
        # default — debug mode adds ~10% overhead per call.
        if os.environ.get("BEO_LOOP_DEBUG") == "1":
            loop.set_debug(True)
            loop.slow_callback_duration = self._warn
            self._logger.info(
                "asyncio debug mode ON (slow_callback_duration=%.0fms) "
                "— stall tracebacks will be logged", self._warn * 1000,
            )
        self._task = loop.create_task(self._run())
        return self

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        # One-line summary so a restart-free operator can see the
        # lifetime max.  Silent samples don't show up in any other log.
        if self.samples > 0:
            self._logger.info(
                "loop_monitor summary: samples=%d stalls=%d max_lag_ms=%.0f",
                self.samples, self.stalls, self.max_lag_ms,
            )

    async def __aenter__(self) -> "LoopMonitor":
        return self.start()

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            before = loop.time()
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._interval
                )
                return  # stop requested during sleep
            except asyncio.TimeoutError:
                pass
            lag = loop.time() - before - self._interval
            self.samples += 1
            lag_ms = lag * 1000
            if lag_ms > self.max_lag_ms:
                self.max_lag_ms = lag_ms
            if lag >= self._warn:
                self.stalls += 1
                self._logger.warning(
                    "event loop stalled %.0fms (interval=%.0fms) — "
                    "a blocking sync call is running on the loop",
                    lag_ms, self._interval * 1000,
                )
