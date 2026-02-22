"""Session management with 3-way handshake.

Implements connection-oriented sessions:
- 3-way handshake (SYN → SYN-ACK → ACK)
- Session state management
- Graceful close with FIN/ACK
"""

import threading
import time
from enum import Enum, auto
from typing import Optional, Callable
from dataclasses import dataclass

from ..datalink.framer import Framer
from ..datalink.frame import Frame, FrameType
from .reliable import ReliableTransport, MessageTransport


class SessionState(Enum):
    """Session connection state."""
    CLOSED = auto()
    SYN_SENT = auto()
    SYN_RECEIVED = auto()
    ESTABLISHED = auto()
    FIN_WAIT = auto()
    CLOSE_WAIT = auto()
    LAST_ACK = auto()
    TIME_WAIT = auto()


@dataclass
class SessionConfig:
    """Configuration for session management."""
    connect_timeout: float = 8.0
    handshake_retries: int = 5
    close_timeout: float = 2.0


class Session:
    """Connection-oriented session over reliable transport."""

    def __init__(
        self,
        transport: ReliableTransport,
        config: Optional[SessionConfig] = None,
    ):
        """Initialize session.

        Args:
            transport: Underlying reliable transport
            config: Session configuration
        """
        self.transport = transport
        self.config = config or SessionConfig()

        self.state = SessionState.CLOSED
        self._lock = threading.Lock()

    @property
    def framer(self) -> Framer:
        """Get underlying framer."""
        return self.transport.framer

    def connect(self) -> bool:
        """Initiate connection (client side).

        Performs 3-way handshake: SYN → SYN-ACK → ACK

        Returns:
            True if connection established
        """
        import sys
        with self._lock:
            if self.state != SessionState.CLOSED:
                return False

            for attempt in range(self.config.handshake_retries):
                # Send SYN
                print(f'DEBUG SESSION connect: Sending SYN (attempt {attempt+1})', file=sys.stderr, flush=True)
                syn = Frame.create_syn()
                self.framer.send_frame(syn)
                self.state = SessionState.SYN_SENT

                # Wait for SYN-ACK
                print(f'DEBUG SESSION connect: Waiting for SYN-ACK', file=sys.stderr, flush=True)
                response = self.framer.wait_for_frame(
                    expected_type=FrameType.SYN_ACK,
                    timeout=self.config.connect_timeout,
                )

                if response is None:
                    print(f'DEBUG SESSION connect: No SYN-ACK received', file=sys.stderr, flush=True)
                    continue

                # Send ACK to complete handshake
                print(f'DEBUG SESSION connect: Got SYN-ACK, sending ACK', file=sys.stderr, flush=True)
                ack = Frame.create_ack(0)
                self.framer.send_frame(ack)
                self.state = SessionState.ESTABLISHED
                print(f'DEBUG SESSION connect: ESTABLISHED', file=sys.stderr, flush=True)

                # Reset transport sequence numbers
                self.transport.reset()

                return True

            self.state = SessionState.CLOSED
            return False

    def accept(self, timeout: float = None) -> bool:
        """Accept incoming connection (server side).

        Waits for SYN and completes handshake: SYN → SYN-ACK → ACK

        Args:
            timeout: Maximum time to wait for connection

        Returns:
            True if connection established
        """
        import sys
        if timeout is None:
            timeout = self.config.connect_timeout * 2

        with self._lock:
            if self.state != SessionState.CLOSED:
                return False

            # Wait for SYN
            print(f'DEBUG SESSION accept: Waiting for SYN (timeout={timeout})', file=sys.stderr, flush=True)
            frame = self.framer.wait_for_frame(
                expected_type=FrameType.SYN,
                timeout=timeout,
            )

            if frame is None:
                print(f'DEBUG SESSION accept: No SYN received', file=sys.stderr, flush=True)
                return False
            print(f'DEBUG SESSION accept: Got SYN!', file=sys.stderr, flush=True)

            self.state = SessionState.SYN_RECEIVED

            # Send SYN-ACK
            syn_ack = Frame.create_syn_ack()
            self.framer.send_frame(syn_ack)

            # Wait for ACK
            ack = self.framer.wait_for_frame(
                expected_type=FrameType.ACK,
                timeout=self.config.connect_timeout,
            )

            if ack is None:
                self.state = SessionState.CLOSED
                return False

            self.state = SessionState.ESTABLISHED

            # Reset transport sequence numbers
            self.transport.reset()

            return True

    def send(self, data: bytes) -> bool:
        """Send data over established session.

        Args:
            data: Data to send

        Returns:
            True if sent successfully
        """
        if self.state != SessionState.ESTABLISHED:
            return False

        return self.transport.send(data)

    def receive(self, timeout: float = None) -> Optional[bytes]:
        """Receive data from session.

        Args:
            timeout: Maximum time to wait

        Returns:
            Received data, or None on timeout/close
        """
        if self.state != SessionState.ESTABLISHED:
            return None

        return self.transport.receive(timeout=timeout)

    def close(self) -> None:
        """Close the session gracefully."""
        with self._lock:
            if self.state != SessionState.ESTABLISHED:
                self.state = SessionState.CLOSED
                return

            self.state = SessionState.FIN_WAIT
            self.transport.close()
            self.state = SessionState.CLOSED

    def reset(self) -> None:
        """Reset the session (force close)."""
        with self._lock:
            frame = Frame.create_rst()
            self.framer.send_frame(frame)
            self.state = SessionState.CLOSED
            self.transport.reset()

    @property
    def is_established(self) -> bool:
        """Check if session is established."""
        return self.state == SessionState.ESTABLISHED

    @property
    def is_closed(self) -> bool:
        """Check if session is closed."""
        return self.state == SessionState.CLOSED


class SessionManager:
    """Manages multiple sessions over a single framer."""

    def __init__(self, framer: Framer):
        """Initialize session manager.

        Args:
            framer: Framer for physical communication
        """
        self.framer = framer
        self._sessions: dict[int, Session] = {}
        self._next_id = 0
        self._lock = threading.Lock()

    def create_session(self) -> Session:
        """Create a new session.

        Returns:
            New session instance
        """
        with self._lock:
            transport = ReliableTransport(self.framer)
            session = Session(transport)

            session_id = self._next_id
            self._next_id += 1
            self._sessions[session_id] = session

            return session

    def get_session(self, session_id: int) -> Optional[Session]:
        """Get session by ID.

        Args:
            session_id: Session identifier

        Returns:
            Session instance, or None if not found
        """
        return self._sessions.get(session_id)

    def close_all(self) -> None:
        """Close all sessions."""
        with self._lock:
            for session in self._sessions.values():
                try:
                    session.close()
                except Exception:
                    pass
            self._sessions.clear()

    def create_client_session(self) -> Optional[Session]:
        """Create and connect a client session.

        Returns:
            Connected session, or None on failure
        """
        session = self.create_session()
        if session.connect():
            return session
        return None

    def accept_server_session(self, timeout: float = None) -> Optional[Session]:
        """Accept an incoming server session.

        Args:
            timeout: Maximum time to wait

        Returns:
            Accepted session, or None on timeout
        """
        session = self.create_session()
        if session.accept(timeout=timeout):
            return session
        return None
