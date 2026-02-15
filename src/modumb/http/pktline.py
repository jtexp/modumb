"""Git pkt-line format encoding and decoding.

Git uses pkt-line format for smart HTTP protocol:
- Each line prefixed with 4-digit hex length (including length itself)
- "0000" is flush-pkt (end of section)
- "0001" is delimiter-pkt (section separator, protocol v2)
- "0002" is response-end-pkt (end of response, protocol v2)

Example:
    001e# service=git-upload-pack\n
    0000
    00a0ref-prefix HEAD\n
    ...
"""

from typing import Iterator, Optional
import re


# Special pkt-line markers
FLUSH_PKT = b'0000'
DELIM_PKT = b'0001'
RESPONSE_END_PKT = b'0002'


class PktLine:
    """Git pkt-line format encoder/decoder."""

    @staticmethod
    def encode_line(data: bytes) -> bytes:
        """Encode a single line in pkt-line format.

        Args:
            data: Line data (without length prefix)

        Returns:
            Encoded pkt-line
        """
        # Length includes the 4-byte prefix
        length = len(data) + 4
        if length > 65535:
            raise ValueError(f"Line too long: {length} bytes")

        return f'{length:04x}'.encode() + data

    @staticmethod
    def encode_flush() -> bytes:
        """Return flush packet (0000)."""
        return FLUSH_PKT

    @staticmethod
    def encode_delim() -> bytes:
        """Return delimiter packet (0001)."""
        return DELIM_PKT

    @staticmethod
    def encode_lines(lines: list[bytes], flush: bool = True) -> bytes:
        """Encode multiple lines in pkt-line format.

        Args:
            lines: List of line data
            flush: If True, append flush packet

        Returns:
            Encoded pkt-lines
        """
        result = b''
        for line in lines:
            result += PktLine.encode_line(line)
        if flush:
            result += FLUSH_PKT
        return result

    @staticmethod
    def decode_line(data: bytes) -> tuple[Optional[bytes], bytes]:
        """Decode a single pkt-line.

        Args:
            data: Encoded data

        Returns:
            Tuple of (decoded line or None for special packets, remaining data)
        """
        if len(data) < 4:
            raise ValueError("Incomplete pkt-line header")

        # Parse length
        try:
            length = int(data[:4], 16)
        except ValueError:
            raise ValueError(f"Invalid pkt-line length: {data[:4]!r}")

        # Handle special packets
        if length == 0:
            return None, data[4:]  # Flush
        if length == 1:
            return None, data[4:]  # Delimiter
        if length == 2:
            return None, data[4:]  # Response end

        if length < 4:
            raise ValueError(f"Invalid pkt-line length: {length}")

        if len(data) < length:
            raise ValueError(f"Incomplete pkt-line: need {length}, have {len(data)}")

        # Extract line data
        line = data[4:length]
        remaining = data[length:]

        return line, remaining

    @staticmethod
    def decode_lines(data: bytes) -> Iterator[Optional[bytes]]:
        """Decode multiple pkt-lines.

        Yields:
            Decoded lines, or None for special packets
        """
        while data:
            if len(data) < 4:
                break

            line, data = PktLine.decode_line(data)
            yield line

    @staticmethod
    def decode_all(data: bytes) -> list[bytes]:
        """Decode all pkt-lines, skipping special packets.

        Returns:
            List of decoded lines
        """
        return [line for line in PktLine.decode_lines(data) if line is not None]

    @staticmethod
    def parse_capability_advertisement(data: bytes) -> dict:
        """Parse capability advertisement from git-upload-pack.

        Returns:
            Dict with 'refs' and 'capabilities'
        """
        refs = {}
        capabilities = set()

        for line in PktLine.decode_lines(data):
            if line is None:
                continue

            # Remove trailing newline
            line = line.rstrip(b'\n')

            # First ref line may contain capabilities after NUL
            if b'\x00' in line:
                ref_part, cap_part = line.split(b'\x00', 1)
                line = ref_part
                capabilities.update(cap_part.decode().split())

            # Parse ref line: <sha1> <refname>
            if b' ' in line:
                parts = line.split(b' ', 1)
                if len(parts) == 2:
                    sha1, refname = parts
                    refs[refname.decode()] = sha1.decode()

        return {
            'refs': refs,
            'capabilities': capabilities,
        }

    @staticmethod
    def build_want_request(want_refs: list[str], capabilities: list[str] = None) -> bytes:
        """Build a git fetch 'want' request.

        Args:
            want_refs: List of SHA1 hashes to fetch
            capabilities: List of capabilities to advertise

        Returns:
            Encoded pkt-line request
        """
        lines = []

        for i, ref in enumerate(want_refs):
            line = f'want {ref}'
            if i == 0 and capabilities:
                line += ' ' + ' '.join(capabilities)
            lines.append((line + '\n').encode())

        # Add done
        lines.append(b'done\n')

        return PktLine.encode_lines(lines, flush=True)

    @staticmethod
    def build_ls_refs_request() -> bytes:
        """Build a git ls-refs request (protocol v2).

        Returns:
            Encoded pkt-line request
        """
        lines = [
            b'command=ls-refs\n',
            b'agent=modumb/0.1\n',
        ]
        return PktLine.encode_lines(lines, flush=True)
