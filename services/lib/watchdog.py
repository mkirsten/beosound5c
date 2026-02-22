"""Systemd watchdog heartbeat for asyncio services.

Sends WATCHDOG=1 to the systemd notify socket at regular intervals.
Silently no-ops when NOTIFY_SOCKET is unset (macOS / dev mode).

Usage:
    from lib.watchdog import watchdog_loop
    asyncio.create_task(watchdog_loop())
"""

import asyncio
import logging
import os
import socket

logger = logging.getLogger(__name__)

_notify_socket = os.environ.get("NOTIFY_SOCKET")


def sd_notify(msg: str):
    """Send a notification message to the systemd notify socket."""
    if not _notify_socket:
        return
    addr = _notify_socket
    if addr[0] == "@":
        addr = "\0" + addr[1:]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.sendto(msg.encode(), addr)
    finally:
        sock.close()


async def watchdog_loop(interval: int = 20):
    """Send WATCHDOG=1 every *interval* seconds.  Call as asyncio.create_task().

    Also sends READY=1 on first invocation so systemd knows the service
    has finished startup (requires Type=notify in the unit file).
    """
    sd_notify("READY=1")
    logger.info("Watchdog started (interval=%ds)", interval)
    while True:
        sd_notify("WATCHDOG=1")
        await asyncio.sleep(interval)
