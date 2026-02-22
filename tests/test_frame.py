"""Tests for frame encoding/decoding."""

import pytest

from modumb.datalink.frame import Frame, FrameType, MAX_PAYLOAD_SIZE


class TestFrame:
    """Test Frame encoding and decoding."""

    def test_encode_decode_data_frame(self):
        """Test DATA frame roundtrip."""
        frame = Frame.create_data(42, b'Hello, World!')
        encoded = frame.encode()
        decoded = Frame.decode(encoded)

        assert decoded is not None
        assert decoded.frame_type == FrameType.DATA
        assert decoded.sequence == 42
        assert decoded.payload == b'Hello, World!'

    def test_encode_decode_ack(self):
        """Test ACK frame roundtrip."""
        frame = Frame.create_ack(123)
        encoded = frame.encode()
        decoded = Frame.decode(encoded)

        assert decoded is not None
        assert decoded.frame_type == FrameType.ACK
        assert decoded.sequence == 123
        assert decoded.payload == b''

    def test_encode_decode_nak(self):
        """Test NAK frame roundtrip."""
        frame = Frame.create_nak(456)
        encoded = frame.encode()
        decoded = Frame.decode(encoded)

        assert decoded is not None
        assert decoded.frame_type == FrameType.NAK
        assert decoded.sequence == 456

    def test_encode_decode_syn(self):
        """Test SYN frame roundtrip."""
        frame = Frame.create_syn()
        encoded = frame.encode()
        decoded = Frame.decode(encoded)

        assert decoded is not None
        assert decoded.frame_type == FrameType.SYN

    def test_encode_decode_syn_ack(self):
        """Test SYN-ACK frame roundtrip."""
        frame = Frame.create_syn_ack()
        encoded = frame.encode()
        decoded = Frame.decode(encoded)

        assert decoded is not None
        assert decoded.frame_type == FrameType.SYN_ACK

    def test_byte_stuffing(self):
        """Test byte stuffing with flag bytes in payload."""
        # Payload containing flag byte (0x7E) and escape byte (0x7D)
        payload = b'\x7E\x7D\x00\xFF'
        frame = Frame.create_data(1, payload)
        encoded = frame.encode()
        decoded = Frame.decode(encoded)

        assert decoded is not None
        assert decoded.payload == payload

    def test_max_payload(self):
        """Test maximum payload size."""
        payload = b'X' * MAX_PAYLOAD_SIZE
        frame = Frame.create_data(0, payload)
        encoded = frame.encode()
        decoded = Frame.decode(encoded)

        assert decoded is not None
        assert decoded.payload == payload

    def test_payload_too_large(self):
        """Test payload exceeding maximum size raises error."""
        with pytest.raises(ValueError):
            Frame.create_data(0, b'X' * (MAX_PAYLOAD_SIZE + 1))

    def test_sequence_wrap(self):
        """Test sequence number at boundaries."""
        for seq in [0, 1, 0xFFFE, 0xFFFF]:
            frame = Frame.create_data(seq, b'test')
            encoded = frame.encode()
            decoded = Frame.decode(encoded)

            assert decoded is not None
            assert decoded.sequence == seq

    def test_invalid_sequence(self):
        """Test invalid sequence number raises error."""
        with pytest.raises(ValueError):
            Frame(FrameType.DATA, -1, b'test')

        with pytest.raises(ValueError):
            Frame(FrameType.DATA, 0x10000, b'test')

    def test_crc_corruption(self):
        """Test CRC detects corruption."""
        frame = Frame.create_data(1, b'Hello')
        encoded = bytearray(frame.encode())

        # Corrupt a byte in the payload area (after preamble + sync + header)
        # Preamble=16, Sync=2, Header=5, so payload starts at 23
        payload_start = 23
        if len(encoded) > payload_start:
            encoded[payload_start] ^= 0xFF

        decoded = Frame.decode(bytes(encoded))

        # Should return None due to CRC failure
        assert decoded is None

    def test_decode_incomplete(self):
        """Test decoding incomplete data."""
        assert Frame.decode(b'') is None
        assert Frame.decode(b'\x7E\x7E') is None
        assert Frame.decode(b'\x7E\x7E\x01\x00') is None

    def test_decode_no_sync(self):
        """Test decoding data without sync pattern."""
        assert Frame.decode(b'\x00\x00\x00\x00\x00') is None

    def test_preamble_included(self):
        """Test that encoded frame includes preamble."""
        frame = Frame.create_data(0, b'test')
        encoded = frame.encode()

        # Should start with preamble (0xAA bytes)
        assert encoded.startswith(b'\xAA' * 8)

    def test_is_control(self):
        """Test is_control method."""
        assert not Frame.create_data(0, b'').is_control()
        assert Frame.create_ack(0).is_control()
        assert Frame.create_nak(0).is_control()
        assert Frame.create_syn().is_control()
        assert Frame.create_syn_ack().is_control()
        assert Frame.create_fin(0).is_control()
        assert Frame.create_rst().is_control()
