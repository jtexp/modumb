"""HTTP server over modem transport.

Implements HTTP/1.1 server over modem session.
"""

import re
import threading
from typing import Optional, Dict, Callable, Tuple
from dataclasses import dataclass

from ..transport.session import Session, SessionManager
from ..transport.reliable import ReliableTransport, timeout_for_baud
from ..datalink.framer import Framer
from ..modem.modem import Modem

# Type for CONNECT handler: receives (session, target_host_port)
ConnectHandler = Callable[[Session, str], None]


@dataclass
class HttpServerRequest:
    """Incoming HTTP request."""
    method: str
    path: str
    headers: Dict[str, str]
    body: bytes

    @classmethod
    def decode(cls, data: bytes) -> Optional["HttpServerRequest"]:
        """Decode request from bytes."""
        # Find header/body separator
        sep = b'\r\n\r\n'
        sep_pos = data.find(sep)
        if sep_pos < 0:
            sep = b'\n\n'
            sep_pos = data.find(sep)
            if sep_pos < 0:
                return None

        header_data = data[:sep_pos].decode('utf-8', errors='replace')
        body = data[sep_pos + len(sep):]

        # Parse request line
        lines = header_data.split('\n')
        if not lines:
            return None

        request_line = lines[0].strip()
        parts = request_line.split(' ')
        if len(parts) < 2:
            return None

        method = parts[0]
        path = parts[1]

        # Parse headers
        headers = {}
        for line in lines[1:]:
            line = line.strip()
            if ':' in line:
                name, value = line.split(':', 1)
                headers[name.strip().lower()] = value.strip()

        return cls(
            method=method,
            path=path,
            headers=headers,
            body=body,
        )

    @property
    def content_length(self) -> Optional[int]:
        """Get Content-Length header value."""
        cl = self.headers.get('content-length')
        if cl:
            try:
                return int(cl)
            except ValueError:
                pass
        return None


@dataclass
class HttpServerResponse:
    """HTTP response to send."""
    status_code: int
    status_message: str
    headers: Dict[str, str]
    body: bytes

    def encode(self) -> bytes:
        """Encode response to bytes."""
        # Status line
        lines = [f'HTTP/1.1 {self.status_code} {self.status_message}']

        # Add Content-Length
        self.headers['Content-Length'] = str(len(self.body))

        # Headers
        for name, value in self.headers.items():
            lines.append(f'{name}: {value}')

        # Blank line before body
        lines.append('')
        lines.append('')

        header_bytes = '\r\n'.join(lines).encode()
        return header_bytes + self.body

    @classmethod
    def ok(cls, body: bytes, content_type: str = 'application/octet-stream') -> "HttpServerResponse":
        """Create 200 OK response."""
        return cls(
            status_code=200,
            status_message='OK',
            headers={'Content-Type': content_type},
            body=body,
        )

    @classmethod
    def not_found(cls, message: str = 'Not Found') -> "HttpServerResponse":
        """Create 404 Not Found response."""
        return cls(
            status_code=404,
            status_message='Not Found',
            headers={'Content-Type': 'text/plain'},
            body=message.encode(),
        )

    @classmethod
    def error(cls, message: str = 'Internal Server Error') -> "HttpServerResponse":
        """Create 500 Internal Server Error response."""
        return cls(
            status_code=500,
            status_message='Internal Server Error',
            headers={'Content-Type': 'text/plain'},
            body=message.encode(),
        )


# Type for request handler
RequestHandler = Callable[[HttpServerRequest], HttpServerResponse]


class HttpServer:
    """HTTP server over modem transport."""

    def __init__(
        self,
        modem: Modem,
        handler: Optional[RequestHandler] = None,
        full_duplex: bool = False,
        connect_handler: Optional[ConnectHandler] = None,
    ):
        """Initialize HTTP server.

        Args:
            modem: Modem for communication
            handler: Request handler function
            full_duplex: If True, skip half-duplex delays throughout stack
            connect_handler: Handler for CONNECT tunneling
        """
        self.modem = modem
        self.handler = handler or self._default_handler
        self._full_duplex = full_duplex
        self.connect_handler = connect_handler

        self._framer: Optional[Framer] = None
        self._session_manager: Optional[SessionManager] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def _default_handler(self, request: HttpServerRequest) -> HttpServerResponse:
        """Default request handler (returns 404)."""
        return HttpServerResponse.not_found()

    def start(self) -> None:
        """Start the HTTP server."""
        if self._running:
            return

        # Initialize layers
        if not self.modem.is_running:
            self.modem.start()

        self._framer = Framer(self.modem, full_duplex=self._full_duplex)
        self._framer.start()

        ack_timeout = timeout_for_baud(self.modem.baud_rate)
        self._session_manager = SessionManager(self._framer, timeout=ack_timeout, full_duplex=self._full_duplex)
        self._running = True

    def stop(self) -> None:
        """Stop the HTTP server."""
        self._running = False

        if self._session_manager:
            self._session_manager.close_all()

        if self._framer:
            self._framer.stop()

    def serve_once(self, timeout: float = 60.0) -> bool:
        """Accept and handle one connection.

        Args:
            timeout: Maximum time to wait for connection

        Returns:
            True if a connection was handled
        """
        import sys
        if not self._running:
            return False

        # Accept connection
        print(f'DEBUG SERVER: Waiting for session...', file=sys.stderr, flush=True)
        session = self._session_manager.accept_server_session(timeout=timeout)
        if session is None:
            print(f'DEBUG SERVER: No session received', file=sys.stderr, flush=True)
            return False

        print(f'DEBUG SERVER: Session established!', file=sys.stderr, flush=True)
        try:
            self._handle_session(session)
        finally:
            session.close()

        return True

    def serve_forever(self, on_ready=None) -> None:
        """Serve connections forever (blocking).

        Args:
            on_ready: Optional callback invoked after start() completes
                      (modem + framer + session manager all initialized).
        """
        self.start()
        if on_ready is not None:
            on_ready()

        while self._running:
            try:
                self.serve_once(timeout=5.0)
            except Exception:
                pass

    def serve_in_background(self) -> None:
        """Start serving in background thread."""
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    def _handle_session(self, session: Session) -> None:
        """Handle a single session (multiple requests)."""
        import sys
        print(f'DEBUG SERVER: Handling session...', file=sys.stderr, flush=True)
        while self._running and session.is_established:
            # Receive request
            print(f'DEBUG SERVER: Waiting for request...', file=sys.stderr, flush=True)
            request_data = self._receive_request(session)
            print(f'DEBUG SERVER: Got request data: {len(request_data) if request_data else 0} bytes', file=sys.stderr, flush=True)
            if not request_data:
                break

            # Parse request
            request = HttpServerRequest.decode(request_data)
            if request is None:
                response = HttpServerResponse.error('Bad Request')
            else:
                # Check if we need more body data
                if request.content_length and len(request.body) < request.content_length:
                    # Receive remaining body
                    while len(request.body) < request.content_length:
                        chunk = session.receive(timeout=10.0)
                        if chunk is None:
                            break
                        request = HttpServerRequest(
                            method=request.method,
                            path=request.path,
                            headers=request.headers,
                            body=request.body + chunk,
                        )

                # Handle request
                try:
                    response = self.handler(request)
                except Exception as e:
                    response = HttpServerResponse.error(str(e))

            # Send response
            response_bytes = response.encode()
            if not session.send(response_bytes):
                break

            # CONNECT tunnel: after sending 200, hand off to connect_handler
            if (request and request.method == 'CONNECT'
                    and response.status_code == 200
                    and self.connect_handler):
                try:
                    self.connect_handler(session, request.path)
                except Exception as e:
                    print(f'DEBUG SERVER: connect_handler error: {e}',
                          file=sys.stderr, flush=True)
                break

            # Check for Connection: close
            if request and request.headers.get('connection', '').lower() == 'close':
                break

    def _receive_request(self, session: Session, timeout: float = 30.0) -> bytes:
        """Receive complete HTTP request."""
        data = bytearray()
        headers_complete = False
        content_length = None

        while True:
            chunk = session.receive(timeout=timeout)
            if chunk is None:
                break

            data.extend(chunk)

            # Check for header completion
            if not headers_complete:
                sep_pos = data.find(b'\r\n\r\n')
                if sep_pos < 0:
                    sep_pos = data.find(b'\n\n')

                if sep_pos >= 0:
                    headers_complete = True
                    header_data = bytes(data[:sep_pos]).decode('utf-8', errors='replace')

                    # Extract Content-Length
                    for line in header_data.split('\n'):
                        if line.lower().startswith('content-length:'):
                            try:
                                content_length = int(line.split(':', 1)[1].strip())
                            except ValueError:
                                pass
                            break

                    # If no body expected, we're done
                    if content_length is None or content_length == 0:
                        break

            # Check if we have complete body
            if headers_complete and content_length is not None:
                sep = b'\r\n\r\n' if b'\r\n\r\n' in data else b'\n\n'
                body_start = data.find(sep)
                if body_start >= 0:
                    body_start += len(sep)
                    body_len = len(data) - body_start
                    if body_len >= content_length:
                        break

        return bytes(data)

    def __enter__(self) -> "HttpServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
