"""Frame encoding and decoding with CRC-16.

Frame format:
+----------+------+------+-----+--------+---------+--------+
| PREAMBLE | SYNC | TYPE | SEQ | LENGTH | PAYLOAD | CRC-16 |
| 8 bytes  | 2B   | 1B   | 2B  | 2B     | 0-256B  | 2B     |
+----------+------+------+-----+--------+---------+--------+

- PREAMBLE: 0xAA bytes for bit synchronization
- SYNC: 0x7E 0x7E frame delimiter
- TYPE: Frame type (DATA, ACK, NAK, SYN, etc.)
- SEQ: Sequence number (16-bit, little-endian)
- LENGTH: Payload length (16-bit, little-endian)
- PAYLOAD: 0-256 bytes of data
- CRC-16: CRC-16-CCITT over TYPE+SEQ+LENGTH+PAYLOAD
"""

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

try:
    import crcmod
    crc16_func = crcmod.predefined.mkCrcFun('crc-ccitt-false')
except ImportError:
    # Fallback CRC-16-CCITT implementation
    def crc16_func(data: bytes) -> int:
        crc = 0xFFFF
        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                if crc & 0x8000:
                    crc = (crc << 1) ^ 0x1021
                else:
                    crc <<= 1
                crc &= 0xFFFF
        return crc


# Frame constants
# Longer preamble for better bit synchronization over audio
PREAMBLE = b'\xAA' * 16  # 16 bytes of alternating bits
SYNC = b'\x7E\x7E'
MAX_PAYLOAD_SIZE = 64  # Reduced to limit clock drift impact
ESCAPE_BYTE = 0x7D
FLAG_BYTE = 0x7E


class FrameType(IntEnum):
    """Frame types for the protocol."""
    DATA = 0x01      # Data frame
    ACK = 0x02       # Acknowledgment
    NAK = 0x03       # Negative acknowledgment
    SYN = 0x10       # Connection request
    SYN_ACK = 0x11   # Connection accept
    FIN = 0x12       # Connection close
    RST = 0x13       # Connection reset


@dataclass
class Frame:
    """Protocol frame with encoding and decoding."""

    frame_type: FrameType
    sequence: int
    payload: bytes

    def __post_init__(self):
        if len(self.payload) > MAX_PAYLOAD_SIZE:
            raise ValueError(f"Payload too large: {len(self.payload)} > {MAX_PAYLOAD_SIZE}")
        if not 0 <= self.sequence <= 0xFFFF:
            raise ValueError(f"Sequence number out of range: {self.sequence}")

    @staticmethod
    def compute_crc(data: bytes) -> int:
        """Compute CRC-16-CCITT checksum."""
        return crc16_func(data)

    @staticmethod
    def _byte_stuff(data: bytes) -> bytes:
        """Apply HDLC-style byte stuffing.

        Escape 0x7E (flag) and 0x7D (escape) bytes.
        """
        result = bytearray()
        for byte in data:
            if byte == FLAG_BYTE or byte == ESCAPE_BYTE:
                result.append(ESCAPE_BYTE)
                result.append(byte ^ 0x20)  # XOR with 0x20
            else:
                result.append(byte)
        return bytes(result)

    @staticmethod
    def _byte_unstuff(data: bytes) -> bytes:
        """Remove HDLC-style byte stuffing."""
        result = bytearray()
        i = 0
        while i < len(data):
            if data[i] == ESCAPE_BYTE and i + 1 < len(data):
                result.append(data[i + 1] ^ 0x20)
                i += 2
            else:
                result.append(data[i])
                i += 1
        return bytes(result)

    def encode(self) -> bytes:
        """Encode frame to bytes for transmission."""
        # Build frame content (TYPE + SEQ + LENGTH + PAYLOAD)
        content = struct.pack(
            '<BHH',
            self.frame_type,
            self.sequence,
            len(self.payload),
        ) + self.payload

        # Compute CRC over content
        crc = self.compute_crc(content)
        content_with_crc = content + struct.pack('<H', crc)

        # Apply byte stuffing
        stuffed = self._byte_stuff(content_with_crc)

        # Add preamble and sync
        return PREAMBLE + SYNC + stuffed

    @classmethod
    def _find_frame_start(cls, data: bytes) -> int:
        """Find the start of frame content after SYNC.

        Tolerates bit errors in SYNC pattern by looking for
        preamble-like bytes followed by frame type.
        """
        # Try exact SYNC match first
        sync_pos = data.find(SYNC)
        if sync_pos >= 0:
            return sync_pos + len(SYNC)

        # Tolerant search: look for preamble-like pattern followed by valid frame type
        # Valid frame types: 0x01, 0x02, 0x03, 0x10, 0x11, 0x12, 0x13
        valid_types = {0x01, 0x02, 0x03, 0x10, 0x11, 0x12, 0x13}

        for i in range(4, len(data) - 7):
            # Check if bytes before look like preamble (0xAA with possible bit errors)
            preamble_score = 0
            for j in range(max(0, i - 6), i):
                byte = data[j]
                # 0xAA has 4 ones and 4 zeros, alternating
                # Similar bytes: 0xAA, 0xA8, 0xAB, 0x2A, 0xAE, 0xEA, 0x55
                ones = bin(byte).count('1')
                if ones >= 3 and ones <= 5:
                    preamble_score += 1

            if preamble_score >= 3:
                # Skip 2 bytes (corrupted SYNC) and check for valid frame type
                content_start = i + 2
                if content_start < len(data):
                    frame_type = data[content_start]
                    if frame_type in valid_types:
                        return content_start

        return -1

    @classmethod
    def decode(cls, data: bytes) -> Optional["Frame"]:
        """Decode frame from received bytes.

        Args:
            data: Raw bytes (should include SYNC but may not include PREAMBLE)

        Returns:
            Decoded Frame, or None if invalid
        """
        # Find frame start (after SYNC)
        content_start = cls._find_frame_start(data)
        if content_start < 0:
            return None

        data = data[content_start:]

        if len(data) < 7:  # Minimum: TYPE(1) + SEQ(2) + LEN(2) + CRC(2)
            return None

        # Remove byte stuffing
        try:
            unstuffed = cls._byte_unstuff(data)
        except Exception:
            return None

        if len(unstuffed) < 7:
            return None

        # Parse header
        try:
            frame_type, sequence, length = struct.unpack('<BHH', unstuffed[:5])
        except struct.error:
            return None

        if length > MAX_PAYLOAD_SIZE:
            return None

        # Check we have enough data
        expected_len = 5 + length + 2  # header + payload + CRC
        if len(unstuffed) < expected_len:
            return None

        # Extract payload and CRC
        payload = unstuffed[5:5 + length]
        received_crc = struct.unpack('<H', unstuffed[5 + length:5 + length + 2])[0]

        # Verify CRC
        content = unstuffed[:5 + length]
        computed_crc = cls.compute_crc(content)

        if received_crc != computed_crc:
            # Try 1-bit error correction: flip each bit in content+CRC
            # and check if CRC matches. Computationally cheap for small frames.
            frame_data = unstuffed[:5 + length + 2]
            corrected = cls._try_correct_1bit(frame_data, 5 + length)
            if corrected is not None:
                content = corrected[:5 + length]
                payload = corrected[5:5 + length]
                frame_type, sequence, length = struct.unpack('<BHH', corrected[:5])
            else:
                # Try 2-bit error correction across entire frame
                corrected = cls._try_correct_2bit(
                    frame_data, 5 + length)
                if corrected is not None:
                    content = corrected[:5 + length]
                    payload = corrected[5:5 + length]
                    frame_type, sequence, length = struct.unpack(
                        '<BHH', corrected[:5])
                else:
                    import sys
                    print(f'DEBUG FRAME: CRC mismatch: received=0x{received_crc:04x} computed=0x{computed_crc:04x} length={length}', file=sys.stderr, flush=True)
                    print(f'DEBUG FRAME: Payload start: {payload[:50].hex() if len(payload) > 50 else payload.hex()}', file=sys.stderr, flush=True)
                    return None

        # Validate frame type
        try:
            frame_type = FrameType(frame_type)
        except ValueError:
            return None

        return cls(
            frame_type=frame_type,
            sequence=sequence,
            payload=payload,
        )

    @classmethod
    def _try_correct_1bit(cls, frame_data: bytes, content_len: int) -> Optional[bytes]:
        """Try to correct a single-bit error in frame data.

        Flips each bit in the frame (content + CRC) and checks if the
        CRC matches. Returns corrected bytes or None.
        """
        data = bytearray(frame_data)
        total_bits = len(data) * 8
        for bit_pos in range(total_bits):
            byte_idx = bit_pos // 8
            bit_idx = bit_pos % 8
            # Flip the bit
            data[byte_idx] ^= (1 << bit_idx)
            # Check CRC
            content = bytes(data[:content_len])
            crc = cls.compute_crc(content)
            received_crc = struct.unpack('<H', bytes(data[content_len:content_len + 2]))[0]
            if crc == received_crc:
                return bytes(data)
            # Flip back
            data[byte_idx] ^= (1 << bit_idx)
        return None

    @classmethod
    def _try_correct_2bit(
        cls, frame_data: bytes, content_len: int
    ) -> Optional[bytes]:
        """Try to correct a 2-bit error anywhere in the frame.

        Flips every pair of bits in content+CRC and checks CRC.
        Validates that the corrected frame type is still valid to
        guard against false positives (probability <1% for typical
        frame sizes with CRC-16).
        """
        valid_types = {0x01, 0x02, 0x03, 0x10, 0x11, 0x12, 0x13}
        data = bytearray(frame_data)
        total_bits = (content_len + 2) * 8  # content + CRC
        for i in range(total_bits):
            byte_i = i // 8
            bit_i = i % 8
            data[byte_i] ^= (1 << bit_i)
            for j in range(i + 1, total_bits):
                byte_j = j // 8
                bit_j = j % 8
                data[byte_j] ^= (1 << bit_j)
                content = bytes(data[:content_len])
                crc = cls.compute_crc(content)
                received_crc = struct.unpack(
                    '<H', bytes(data[content_len:content_len + 2]))[0]
                if crc == received_crc and data[0] in valid_types:
                    return bytes(data)
                data[byte_j] ^= (1 << bit_j)
            data[byte_i] ^= (1 << bit_i)
        return None

    @classmethod
    def create_data(cls, sequence: int, data: bytes) -> "Frame":
        """Create a DATA frame."""
        return cls(FrameType.DATA, sequence, data)

    @classmethod
    def create_ack(cls, sequence: int) -> "Frame":
        """Create an ACK frame."""
        return cls(FrameType.ACK, sequence, b'')

    @classmethod
    def create_nak(cls, sequence: int) -> "Frame":
        """Create a NAK frame."""
        return cls(FrameType.NAK, sequence, b'')

    @classmethod
    def create_syn(cls) -> "Frame":
        """Create a SYN frame for connection setup."""
        return cls(FrameType.SYN, 0, b'')

    @classmethod
    def create_syn_ack(cls) -> "Frame":
        """Create a SYN-ACK frame for connection accept."""
        return cls(FrameType.SYN_ACK, 0, b'')

    @classmethod
    def create_fin(cls, sequence: int) -> "Frame":
        """Create a FIN frame for connection close."""
        return cls(FrameType.FIN, sequence, b'')

    @classmethod
    def create_rst(cls) -> "Frame":
        """Create a RST frame for connection reset."""
        return cls(FrameType.RST, 0, b'')

    def is_control(self) -> bool:
        """Check if this is a control frame (not DATA)."""
        return self.frame_type != FrameType.DATA

    def __repr__(self) -> str:
        payload_repr = f"{len(self.payload)} bytes" if len(self.payload) > 10 else repr(self.payload)
        return f"Frame({self.frame_type.name}, seq={self.sequence}, payload={payload_repr})"
