"""Link-device presence tracking in the MasterLink master role.

``audio_on()`` only asserts ML distribute when ``wants_distribute()`` is
True, and that is gated on having seen a telegram from a link device.  A
device that only emits telegram types the master role has no handler for
still has to count: it is audibly on the bus either way.
"""

import importlib

import pytest

mlm = importlib.import_module("lib.masterlink_master")


class StubPC2:
    """Enough of PC2Device for the dispatch paths under test.

    ``session``/``loop`` are None, which makes the wake tracker and the
    router forwarder bail out early — neither is what these tests are
    about.  Replies are recorded rather than sent.
    """

    session = None
    loop = None
    OUR_NODE_ID = 0xC0

    def __init__(self):
        self.sent = []

    @staticmethod
    def node_label(node):
        return f"0x{node:02X}"

    def send_ml_telegram(self, **kw):
        self.sent.append(kw)


LINK_NODE = 0xC1        # a real link device
BROADCAST = 0x80        # "ALL"

# LOCK_MANAGER_COMMAND, which a BeoLab 3500 emits continuously. No handler
# exists for it, so it exercises the fallthrough.
UNHANDLED_TTYPE = 0x0A
UNHANDLED_PTYPE = 0x5C


@pytest.fixture
def role():
    return mlm.MasterRole(StubPC2())


@pytest.fixture
def pc2(role):
    return role.pc2


def _send(role, ptype, src_node, ttype=UNHANDLED_TTYPE, payload=b""):
    role.handle_telegram(ttype, ptype, src_node, 0xC0, 0x00, payload)


def test_no_distribute_before_any_link_device_is_heard(role):
    assert role.wants_distribute() is False


def test_unhandled_telegram_counts_as_link_presence(role):
    _send(role, UNHANDLED_PTYPE, LINK_NODE)
    assert role.wants_distribute() is True


def test_broadcast_address_is_not_a_link_device(role):
    _send(role, UNHANDLED_PTYPE, BROADCAST)
    assert role.wants_distribute() is False


def test_presence_expires_after_the_window(role, monkeypatch):
    _send(role, UNHANDLED_PTYPE, LINK_NODE)
    seen_at = role._last_link_seen
    monkeypatch.setattr(
        mlm.time, "monotonic",
        lambda: seen_at + mlm.LINK_PRESENCE_WINDOW + 1)
    assert role.wants_distribute() is False


def test_handled_telegrams_still_mark_presence(role, pc2):
    """The fallthrough is additive: it must not displace the existing
    per-handler calls, and must not swallow their replies."""
    _send(role, 0x08, LINK_NODE, ttype=0x0B)      # AUDIO_BUS request
    assert role.wants_distribute() is True
    assert [t["payload_type"] for t in pc2.sent] == [0x08]


def test_unhandled_telegram_sends_no_reply(role, pc2):
    """Presence is all we infer from it — there is still no handler, so
    nothing should go back on the bus."""
    _send(role, UNHANDLED_PTYPE, LINK_NODE)
    assert pc2.sent == []
