"""Tunnel chunk protocol for CONNECT tunneling.

Shared by both local proxy and remote relay. Uses length-prefixed
binary chunks over the existing modem session:

    [4-byte LE uint32 length][data bytes]

Length=0 is the close signal. Proxy always sends first (client->server),
relay responds (server->client). This strict alternation is half-duplex safe.
"""

import struct
import sys
from typing import Optional

from ..transport.session import Session

MAX_TUNNEL_CHUNK = 2048

# Max data bytes per chunk sent over modem. Keep chunks small enough to
# limit back-to-back frame bursts in half-duplex HTTPS tunneling.
# 96 data + 4-byte header = 100 bytes (2 frames at 64B payload).
MODEM_CHUNK_SIZE = 96

# Sentinel value for close signal (distinct from length=0 keepalive)
_CLOSE_SENTINEL = 0xFFFFFFFF


def send_chunk(session: Session, data: bytes) -> bool:
    """Send a length-prefixed chunk over the session.

    Length=0 is a valid keepalive (empty data). Close uses a sentinel.

    Args:
        session: Modem session
        data: Data to send (may be empty for keep-alive)

    Returns:
        True if sent successfully
    """
    header = struct.pack('<I', len(data))
    return session.send(header + data)


def receive_chunk(session: Session, timeout: float = 30.0) -> Optional[bytes]:
    """Receive a length-prefixed chunk from the session.

    Buffers session.receive() calls until a full chunk is assembled.

    Args:
        session: Modem session
        timeout: Timeout for each receive call

    Returns:
        Chunk data (b'' for empty keepalive, None for close/error/timeout)
    """
    buf = bytearray()

    # Read header (4 bytes)
    while len(buf) < 4:
        chunk = session.receive(timeout=timeout)
        if chunk is None:
            return None
        buf.extend(chunk)

    length = struct.unpack('<I', buf[:4])[0]

    # Close signal
    if length == _CLOSE_SENTINEL:
        return None

    # Empty keepalive
    if length == 0:
        return b''

    # Read body
    body_buf = buf[4:]  # May have partial body from header read
    while len(body_buf) < length:
        chunk = session.receive(timeout=timeout)
        if chunk is None:
            return None
        body_buf.extend(chunk)

    return bytes(body_buf[:length])


def send_close(session: Session) -> bool:
    """Send close signal (sentinel value).

    Args:
        session: Modem session

    Returns:
        True if sent successfully
    """
    return session.send(struct.pack('<I', _CLOSE_SENTINEL))
