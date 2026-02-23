"""Tests for framer multi-frame extraction behavior."""

from modumb.datalink.frame import Frame
from modumb.datalink.framer import Framer


class _FakeModem:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.is_running = True

    def start(self):
        self.is_running = True

    def receive(self, timeout=None):  # noqa: ARG002
        if self._payloads:
            return self._payloads.pop(0)
        return b""

    def send(self, data, blocking=True):  # noqa: ARG002
        return True


def test_receive_frame_queues_second_frame_from_same_blob():
    f1 = Frame.create_ack(3).encode()
    f2 = Frame.create_data(7, b"abc").encode()
    modem = _FakeModem([f1 + f2])
    framer = Framer(modem)

    first = framer.receive_frame(timeout=0.1)
    second = framer.receive_frame(timeout=0.1)

    assert first is not None
    assert second is not None
    assert first.frame_type.name == "ACK"
    assert first.sequence == 3
    assert second.frame_type.name == "DATA"
    assert second.sequence == 7
    assert second.payload == b"abc"
