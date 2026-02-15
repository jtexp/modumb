"""Reliable transport with Stop-and-Wait ARQ.

Implements reliable delivery over the unreliable frame layer using:
- Stop-and-Wait ARQ with ACK/NAK
- Retransmission on timeout
- Message fragmentation and reassembly
"""

import threading
import time
from typing import Optional, Callable
from dataclasses import dataclass

from ..datalink.framer import Framer
from ..datalink.frame import Frame, FrameType, MAX_PAYLOAD_SIZE


# ARQ parameters
DEFAULT_TIMEOUT = 3.0       # Timeout for ACK (seconds)
DEFAULT_RETRIES = 3         # Maximum retransmission attempts
DEFAULT_FRAGMENT_SIZE = MAX_PAYLOAD_SIZE  # Maximum fragment size


@dataclass
class TransportStats:
    """Statistics for transport layer."""
    frames_sent: int = 0
    frames_received: int = 0
    retransmissions: int = 0
    timeouts: int = 0
    ack_received: int = 0
    nak_received: int = 0


class ReliableTransport:
    """Reliable transport using Stop-and-Wait ARQ."""

    def __init__(
        self,
        framer: Framer,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        fragment_size: int = DEFAULT_FRAGMENT_SIZE,
    ):
        """Initialize reliable transport.

        Args:
            framer: Framer instance for frame transmission
            timeout: ACK timeout in seconds
            retries: Maximum retransmission attempts
            fragment_size: Maximum payload size per frame
        """
        self.framer = framer
        self.timeout = timeout
        self.retries = retries
        self.fragment_size = min(fragment_size, MAX_PAYLOAD_SIZE)

        # Sequence numbers (16-bit, wrapping)
        self._tx_seq = 0
        self._rx_seq = 0

        # Statistics
        self.stats = TransportStats()

        # Threading
        self._lock = threading.Lock()

    def _next_seq(self) -> int:
        """Get next transmit sequence number."""
        seq = self._tx_seq
        self._tx_seq = (self._tx_seq + 1) & 0xFFFF
        return seq

    def send(self, data: bytes) -> bool:
        """Send data reliably with ARQ.

        Args:
            data: Data to send (will be fragmented if needed)

        Returns:
            True if all data sent and acknowledged, False on failure
        """
        with self._lock:
            # Fragment data if needed
            fragments = self._fragment(data)

            for fragment in fragments:
                if not self._send_fragment(fragment):
                    return False

            return True

    def _fragment(self, data: bytes) -> list[bytes]:
        """Fragment data into chunks."""
        if len(data) <= self.fragment_size:
            return [data]

        fragments = []
        for i in range(0, len(data), self.fragment_size):
            fragments.append(data[i:i + self.fragment_size])
        return fragments

    def _send_fragment(self, data: bytes) -> bool:
        """Send a single fragment with Stop-and-Wait ARQ.

        Returns:
            True if acknowledged, False on failure
        """
        seq = self._next_seq()
        frame = Frame.create_data(seq, data)

        for attempt in range(self.retries + 1):
            # Send frame
            self.framer.send_frame(frame)
            self.stats.frames_sent += 1

            if attempt > 0:
                self.stats.retransmissions += 1

            # Wait for ACK/NAK
            response = self.framer.wait_for_frame(timeout=self.timeout)

            if response is None:
                self.stats.timeouts += 1
                continue

            if response.frame_type == FrameType.ACK:
                if response.sequence == seq:
                    self.stats.ack_received += 1
                    return True
                # Wrong sequence, ignore

            elif response.frame_type == FrameType.NAK:
                self.stats.nak_received += 1
                # Retransmit immediately
                continue

            elif response.frame_type == FrameType.RST:
                # Connection reset
                return False

        return False

    def receive(self, timeout: float = None) -> Optional[bytes]:
        """Receive data reliably.

        Handles ACK/NAK responses automatically.

        Args:
            timeout: Maximum time to wait

        Returns:
            Received data, or None on timeout/error
        """
        if timeout is None:
            timeout = self.timeout * 2

        deadline = time.time() + timeout
        received_data = bytearray()

        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break

            frame = self.framer.receive_frame(timeout=remaining)

            if frame is None:
                continue

            self.stats.frames_received += 1

            if frame.frame_type == FrameType.DATA:
                # Check sequence number
                if frame.sequence == self._rx_seq:
                    # Expected frame, accept it
                    received_data.extend(frame.payload)
                    self._rx_seq = (self._rx_seq + 1) & 0xFFFF

                    # Send ACK
                    self.framer.send_ack(frame.sequence)

                    # Return data for this frame
                    return bytes(frame.payload)

                elif frame.sequence < self._rx_seq:
                    # Duplicate frame, re-ACK
                    self.framer.send_ack(frame.sequence)

                else:
                    # Out of order, NAK
                    self.framer.send_nak(self._rx_seq)

            elif frame.frame_type == FrameType.FIN:
                # Connection closing
                self.framer.send_ack(frame.sequence)
                return None

            elif frame.frame_type == FrameType.RST:
                # Connection reset
                return None

        return None

    def receive_all(self, timeout: float = None) -> bytes:
        """Receive all available data until timeout or connection close.

        Args:
            timeout: Maximum time to wait

        Returns:
            All received data
        """
        if timeout is None:
            timeout = self.timeout * 4

        deadline = time.time() + timeout
        received_data = bytearray()

        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break

            data = self.receive(timeout=min(remaining, self.timeout))
            if data is None:
                break
            received_data.extend(data)

        return bytes(received_data)

    def reset(self) -> None:
        """Reset transport state."""
        with self._lock:
            self._tx_seq = 0
            self._rx_seq = 0
            self.stats = TransportStats()

    def close(self) -> None:
        """Close the transport (send FIN)."""
        with self._lock:
            seq = self._next_seq()
            frame = Frame.create_fin(seq)
            self.framer.send_frame(frame)

            # Wait for ACK
            self.framer.wait_for_frame(
                expected_type=FrameType.ACK,
                expected_seq=seq,
                timeout=self.timeout
            )


class MessageTransport:
    """Message-oriented transport with length-prefix framing."""

    def __init__(self, transport: ReliableTransport):
        """Initialize message transport.

        Args:
            transport: Underlying reliable transport
        """
        self.transport = transport

    def send_message(self, message: bytes) -> bool:
        """Send a complete message.

        Args:
            message: Message to send

        Returns:
            True if sent successfully
        """
        # Prepend 4-byte length header
        length = len(message)
        header = length.to_bytes(4, 'little')
        return self.transport.send(header + message)

    def receive_message(self, timeout: float = None) -> Optional[bytes]:
        """Receive a complete message.

        Args:
            timeout: Maximum time to wait

        Returns:
            Complete message, or None on timeout/error
        """
        # First receive length header
        header_data = bytearray()
        while len(header_data) < 4:
            data = self.transport.receive(timeout=timeout)
            if data is None:
                return None
            header_data.extend(data)

        length = int.from_bytes(header_data[:4], 'little')

        # Receive message body
        message = bytearray(header_data[4:])
        while len(message) < length:
            data = self.transport.receive(timeout=timeout)
            if data is None:
                return None
            message.extend(data)

        return bytes(message[:length])
