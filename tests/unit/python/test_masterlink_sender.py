"""The masterlink sender waits for messages instead of polling.

The sender loop used to call MessageQueue.get() every millisecond, which
kept beo-masterlink at ~13% CPU on a Pi 4 even with no PC2 attached.  It
now sleeps on an asyncio.Event that MessageQueue.add() sets from the
sniffer thread.
"""

import asyncio
import importlib
import sys
import threading
import types

import pytest


@pytest.fixture
def ml(monkeypatch):
    # pyusb needs libusb and real hardware; the queue logic needs neither.
    usb = types.ModuleType("usb")
    usb.core = types.ModuleType("usb.core")
    usb.util = types.ModuleType("usb.util")
    monkeypatch.setitem(sys.modules, "usb", usb)
    monkeypatch.setitem(sys.modules, "usb.core", usb.core)
    monkeypatch.setitem(sys.modules, "usb.util", usb.util)
    monkeypatch.delitem(sys.modules, "masterlink", raising=False)
    return importlib.import_module("masterlink")


def test_add_calls_on_add(ml):
    q = ml.MessageQueue()
    calls = []
    q.on_add = lambda: calls.append(1)
    q.add({"key_name": "play"})
    assert calls == [1]
    assert q.get()["key_name"] == "play"
    assert q.get() is None


def test_add_without_on_add_is_fine(ml):
    q = ml.MessageQueue()
    q.add({"key_name": "play"})
    assert q.size() == 1


def _sender(ml, loop):
    """A PC2Device with only what _async_sender_loop touches."""
    dev = ml.PC2Device.__new__(ml.PC2Device)
    dev.running = True
    dev.loop = loop
    dev.message_queue = ml.MessageQueue()
    dev._queue_event = asyncio.Event()
    dev.message_queue.on_add = dev._wake_sender
    dev.sent = []
    gets = [0]
    real_get = dev.message_queue.get

    def counting_get():
        gets[0] += 1
        return real_get()

    dev.message_queue.get = counting_get

    async def fake_send(message):
        dev.sent.append(message["key_name"])

    dev._send_webhook_async = fake_send
    return dev, gets


def test_sender_idles_and_wakes_on_add_from_other_thread(ml):
    loop = asyncio.new_event_loop()
    try:
        dev, gets = _sender(ml, loop)

        async def scenario():
            task = asyncio.ensure_future(dev._async_sender_loop())
            await asyncio.sleep(0.3)
            # Idle: a polling loop would have called get() ~300 times.
            assert gets[0] <= 3
            threading.Thread(
                target=dev.message_queue.add, args=({"key_name": "play"},)
            ).start()
            for _ in range(50):
                if dev.sent:
                    break
                await asyncio.sleep(0.01)
            dev.running = False
            dev._queue_event.set()
            await asyncio.wait_for(task, timeout=2)

        loop.run_until_complete(scenario())
        assert dev.sent == ["play"]
    finally:
        loop.close()


def test_wake_sender_ends_idle_loop_on_shutdown(ml):
    """stop_sniffing() wakes the idle sender so it exits without a message."""
    loop = asyncio.new_event_loop()
    try:
        dev, _ = _sender(ml, loop)

        async def scenario():
            task = asyncio.ensure_future(dev._async_sender_loop())
            await asyncio.sleep(0.05)
            dev.running = False
            threading.Thread(target=dev._wake_sender).start()
            await asyncio.wait_for(task, timeout=0.5)

        loop.run_until_complete(scenario())
    finally:
        loop.close()
