"""Frame transmission and reception handler.

Handles the transmission and reception of frames over the modem,
including preamble detection and frame synchronization.
"""

import threading
import queue
import time
from typing import Optional, Callable, Iterator

from .frame import Frame, FrameType, PREAMBLE, SYNC
from ..modem.modem import Modem


class Framer:
    """Frame transmission and reception over modem."""

    def __init__(
        self,
        modem: Modem,
        frame_timeout: float = 2.0,
        tx_delay: float = 0.05,
    ):
        """Initialize framer.

        Args:
            modem: Modem instance for physical layer
            frame_timeout: Timeout for receiving a frame
            tx_delay: Delay before transmitting (for half-duplex)
        """
        self.modem = modem
        self.frame_timeout = frame_timeout
        self.tx_delay = tx_delay

        # Receive state
        self._rx_buffer = bytearray()
        self._rx_callback: Optional[Callable[[Frame], None]] = None
        self._rx_queue: queue.Queue[Frame] = queue.Queue()

        # Threading
        self._lock = threading.Lock()
        self._rx_thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        """Start the framer."""
        if not self.modem.is_running:
            self.modem.start()

        self._running = True

    def stop(self) -> None:
        """Stop the framer."""
        self._running = False

    def send_frame(self, frame: Frame) -> None:
        """Send a frame over the modem.

        Args:
            frame: Frame to send
        """
        import sys
        # Encode frame to bytes
        data = frame.encode()
        print(f'DEBUG FRAMER: Sending frame type={frame.frame_type.name} seq={frame.sequence} ({len(data)} bytes)', file=sys.stderr, flush=True)

        # Small delay for half-duplex timing
        if self.tx_delay > 0:
            time.sleep(self.tx_delay)

        # Send via modem
        self.modem.send(data, blocking=True)
        print(f'DEBUG FRAMER: Frame sent', file=sys.stderr, flush=True)

    def receive_frame(self, timeout: float = None) -> Optional[Frame]:
        """Receive a frame from the modem.

        Args:
            timeout: Maximum time to wait (uses default if None)

        Returns:
            Received Frame, or None on timeout/error
        """
        import sys
        if timeout is None:
            timeout = self.frame_timeout

        # First, check the queue for already-received frames
        try:
            return self._rx_queue.get_nowait()
        except queue.Empty:
            pass

        # Receive raw data from modem
        data = self.modem.receive(timeout=timeout)

        if not data or len(data) == 0:
            print(f'DEBUG FRAMER: No data received', file=sys.stderr, flush=True)
            return None

        # Try to decode frame
        frame = Frame.decode(data)
        if frame:
            print(f'DEBUG FRAMER: Received frame type={frame.frame_type.name} seq={frame.sequence}', file=sys.stderr, flush=True)
        else:
            print(f'DEBUG FRAMER: Failed to decode {len(data)} bytes: {data[:50].hex()}...', file=sys.stderr, flush=True)
        return frame

    def receive_frame_raw(self, timeout: float = None) -> tuple[Optional[Frame], bytes]:
        """Receive a frame and return raw bytes too.

        Args:
            timeout: Maximum time to wait

        Returns:
            Tuple of (Frame or None, raw bytes)
        """
        if timeout is None:
            timeout = self.frame_timeout

        data = self.modem.receive(timeout=timeout)

        if not data:
            return None, b''

        frame = Frame.decode(data)
        return frame, data

    def send_data(self, sequence: int, data: bytes) -> None:
        """Send a DATA frame.

        Args:
            sequence: Sequence number
            data: Payload data
        """
        frame = Frame.create_data(sequence, data)
        self.send_frame(frame)

    def send_ack(self, sequence: int) -> None:
        """Send an ACK frame.

        Args:
            sequence: Sequence number being acknowledged
        """
        frame = Frame.create_ack(sequence)
        self.send_frame(frame)

    def send_nak(self, sequence: int) -> None:
        """Send a NAK frame.

        Args:
            sequence: Sequence number being rejected
        """
        frame = Frame.create_nak(sequence)
        self.send_frame(frame)

    def wait_for_frame(
        self,
        expected_type: Optional[FrameType] = None,
        expected_seq: Optional[int] = None,
        timeout: float = None,
    ) -> Optional[Frame]:
        """Wait for a specific type of frame.

        Args:
            expected_type: Expected frame type, or None for any
            expected_seq: Expected sequence number, or None for any
            timeout: Maximum time to wait

        Returns:
            Matching frame, or None on timeout
        """
        if timeout is None:
            timeout = self.frame_timeout

        deadline = time.time() + timeout

        # First check if we have a matching frame already queued
        queued = []
        while not self._rx_queue.empty():
            try:
                frame = self._rx_queue.get_nowait()
                if self._frame_matches(frame, expected_type, expected_seq):
                    # Put non-matching frames back
                    for f in queued:
                        self._rx_queue.put(f)
                    return frame
                queued.append(frame)
            except queue.Empty:
                break
        # Put non-matching frames back
        for f in queued:
            self._rx_queue.put(f)

        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break

            frame = self.receive_frame(timeout=remaining)

            if frame is None:
                continue

            # Check if frame matches criteria
            if self._frame_matches(frame, expected_type, expected_seq):
                return frame

            # Queue non-matching frames for later
            self._rx_queue.put(frame)

        return None

    def _frame_matches(
        self,
        frame: Frame,
        expected_type: Optional[FrameType],
        expected_seq: Optional[int],
    ) -> bool:
        """Check if frame matches criteria."""
        if expected_type is not None and frame.frame_type != expected_type:
            return False
        if expected_seq is not None and frame.sequence != expected_seq:
            return False
        return True

    def exchange(self, frame: Frame, timeout: float = None) -> Optional[Frame]:
        """Send a frame and wait for response.

        Args:
            frame: Frame to send
            timeout: Maximum time to wait for response

        Returns:
            Response frame, or None on timeout
        """
        self.send_frame(frame)
        return self.receive_frame(timeout=timeout)

    def set_receive_callback(self, callback: Optional[Callable[[Frame], None]]) -> None:
        """Set callback for received frames.

        Args:
            callback: Function to call with received frames
        """
        self._rx_callback = callback

    @property
    def is_running(self) -> bool:
        """Check if framer is running."""
        return self._running

    def __enter__(self) -> "Framer":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


class FrameIterator:
    """Iterator for receiving frames with timeout."""

    def __init__(self, framer: Framer, timeout: float = 5.0):
        self.framer = framer
        self.timeout = timeout

    def __iter__(self) -> Iterator[Frame]:
        return self

    def __next__(self) -> Frame:
        frame = self.framer.receive_frame(timeout=self.timeout)
        if frame is None:
            raise StopIteration
        return frame
