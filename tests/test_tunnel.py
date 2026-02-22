"""Tests for tunnel chunk protocol."""

import struct
import pytest
from unittest.mock import MagicMock

from modumb.proxy.tunnel import send_chunk, receive_chunk, send_close, MAX_TUNNEL_CHUNK


def _mock_session(receive_returns=None):
    """Create a mock session with configurable receive behavior."""
    session = MagicMock()
    session.send = MagicMock(return_value=True)
    if receive_returns is not None:
        session.receive = MagicMock(side_effect=receive_returns)
    return session


class TestSendChunk:

    def test_sends_length_prefixed_data(self):
        session = _mock_session()
        data = b"hello"
        result = send_chunk(session, data)
        assert result is True
        sent = session.send.call_args[0][0]
        assert sent[:4] == struct.pack('<I', 5)
        assert sent[4:] == b"hello"

    def test_sends_empty_close_signal(self):
        session = _mock_session()
        result = send_close(session)
        assert result is True
        sent = session.send.call_args[0][0]
        assert sent == struct.pack('<I', 0)

    def test_returns_false_on_send_failure(self):
        session = _mock_session()
        session.send.return_value = False
        result = send_chunk(session, b"data")
        assert result is False


class TestReceiveChunk:

    def test_receives_data_in_one_call(self):
        data = b"hello world"
        payload = struct.pack('<I', len(data)) + data
        session = _mock_session(receive_returns=[payload])
        result = receive_chunk(session)
        assert result == data

    def test_receives_header_and_body_separately(self):
        data = b"hello"
        header = struct.pack('<I', len(data))
        session = _mock_session(receive_returns=[header, data])
        result = receive_chunk(session)
        assert result == data

    def test_receives_close_signal(self):
        header = struct.pack('<I', 0)
        session = _mock_session(receive_returns=[header])
        result = receive_chunk(session)
        assert result == b''

    def test_returns_none_on_timeout(self):
        session = _mock_session(receive_returns=[None])
        result = receive_chunk(session)
        assert result is None

    def test_fragmented_header(self):
        data = b"test"
        header = struct.pack('<I', len(data))
        # Header split across two receives
        session = _mock_session(receive_returns=[header[:2], header[2:], data])
        result = receive_chunk(session)
        assert result == data

    def test_fragmented_body(self):
        data = b"abcdefghij"
        header = struct.pack('<I', len(data))
        # Body in three parts
        session = _mock_session(receive_returns=[header, data[:3], data[3:7], data[7:]])
        result = receive_chunk(session)
        assert result == data

    def test_header_and_partial_body_in_first_read(self):
        data = b"hello world"
        header = struct.pack('<I', len(data))
        # Header + first part of body in one read
        first = header + data[:5]
        session = _mock_session(receive_returns=[first, data[5:]])
        result = receive_chunk(session)
        assert result == data

    def test_timeout_during_body_read(self):
        data = b"hello world"
        header = struct.pack('<I', len(data))
        # Header arrives, then partial body, then timeout
        session = _mock_session(receive_returns=[header, data[:3], None])
        result = receive_chunk(session)
        assert result is None
