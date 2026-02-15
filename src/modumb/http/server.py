"""HTTP server over modem transport.

Implements HTTP/1.1 server for Git smart HTTP protocol.
"""

import re
import threading
from typing import Optional, Dict, Callable, Tuple
from dataclasses import dataclass

from ..transport.session import Session, SessionManager
from ..transport.reliable import ReliableTransport
from ..datalink.framer import Framer
from ..modem.modem import Modem


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
    ):
        """Initialize HTTP server.

        Args:
            modem: Modem for communication
            handler: Request handler function
        """
        self.modem = modem
        self.handler = handler or self._default_handler

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

        self._framer = Framer(self.modem)
        self._framer.start()

        self._session_manager = SessionManager(self._framer)
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
        if not self._running:
            return False

        # Accept connection
        session = self._session_manager.accept_server_session(timeout=timeout)
        if session is None:
            return False

        try:
            self._handle_session(session)
        finally:
            session.close()

        return True

    def serve_forever(self) -> None:
        """Serve connections forever (blocking)."""
        self.start()

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
        while self._running and session.is_established:
            # Receive request
            request_data = self._receive_request(session)
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


def main():
    """Run the modem git server."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description='Modem Git Server',
        epilog='Use "modem-audio devices" to list available audio devices.'
    )
    parser.add_argument('repo_path', help='Path to git repository')
    parser.add_argument('--loopback', action='store_true',
                       help='Use loopback audio (no hardware)')
    parser.add_argument('--audible', action='store_true',
                       help='Play audio even in loopback mode (demo)')
    parser.add_argument('-i', '--input-device', type=int, metavar='N',
                       help='Input device index (microphone)')
    parser.add_argument('-o', '--output-device', type=int, metavar='N',
                       help='Output device index (speaker)')
    args = parser.parse_args()

    # Import git server handler
    from ..git.smart_http import create_server_handler

    # Create modem
    modem = Modem(
        loopback=args.loopback,
        audible=args.audible,
        input_device=args.input_device,
        output_device=args.output_device,
    )

    # Create server
    handler = create_server_handler(args.repo_path)
    server = HttpServer(modem, handler=handler)

    print(f'Starting modem git server for {args.repo_path}')
    print('Listening for connections...')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down...')
        server.stop()


if __name__ == '__main__':
    main()
