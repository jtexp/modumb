"""Local proxy — Machine A (no internet).

Accepts HTTP requests from browser/curl on localhost, forwards them
over the modem session to the remote relay, returns the response.
"""

import sys
import threading
import io
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Optional

from ..http.client import HttpClient, HttpResponse
from ..transport.session import SessionManager, Session
from ..transport.reliable import ReliableTransport
from ..datalink.framer import Framer
from ..modem.modem import Modem
from ..modem.profiles import get_profile, AudioProfile
from .config import ProxyConfig


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Threaded HTTP server for handling concurrent browser connections."""
    daemon_threads = True


class LocalProxy:
    """Local proxy server — browser connects here, requests go over modem."""

    def __init__(self, config: Optional[ProxyConfig] = None):
        self.config = config or ProxyConfig()
        self._modem: Optional[Modem] = None
        self._framer: Optional[Framer] = None
        self._session_mgr: Optional[SessionManager] = None
        self._session: Optional[Session] = None
        self._http_client: Optional[HttpClient] = None
        self._http_server: Optional[_ThreadingHTTPServer] = None
        self._modem_lock = threading.Lock()  # Serialise modem access (half-duplex)

    def _create_modem(self) -> Modem:
        """Create a Modem configured from our ProxyConfig."""
        profile = get_profile(self.config.mode)
        loopback = self.config.mode == "loopback"
        return Modem(
            loopback=loopback,
            audible=self.config.audible,
            input_device=self.config.input_device,
            output_device=self.config.output_device,
            profile=profile,
        )

    def _ensure_session(self) -> bool:
        """Establish modem session if not already connected.

        Returns:
            True if session is established
        """
        if self._session and self._session.is_established:
            return True

        print("Connecting modem session...", file=sys.stderr, flush=True)

        self._modem = self._create_modem()
        self._modem.start()

        self._framer = Framer(self._modem)
        self._framer.start()

        self._session_mgr = SessionManager(self._framer)
        self._session = self._session_mgr.create_client_session()

        if self._session is None:
            print("ERROR: Failed to establish modem session", file=sys.stderr, flush=True)
            return False

        self._http_client = HttpClient(self._session, user_agent="modumb-proxy/0.2")
        print("Modem session established!", file=sys.stderr, flush=True)
        return True

    def _forward_request(self, method: str, url: str, headers: dict,
                         body: bytes) -> Optional[HttpResponse]:
        """Forward a request over the modem to the remote relay.

        Args:
            method: HTTP method
            url: Full URL (absolute URI for proxy)
            headers: Request headers
            body: Request body

        Returns:
            HttpResponse from relay, or None on failure
        """
        with self._modem_lock:
            if not self._ensure_session():
                return None
            return self._http_client.request(
                method=method,
                path=url,  # Send absolute URI so relay knows the target
                headers=headers,
                body=body,
                timeout=self.config.request_timeout,
            )

    def _make_handler(self):
        """Create a request handler class bound to this proxy instance."""
        proxy = self

        class ProxyHandler(BaseHTTPRequestHandler):
            """HTTP request handler that forwards to modem relay."""

            # Suppress default logging to stderr (we do our own)
            def log_message(self, format, *args):
                print(f"PROXY: {format % args}", file=sys.stderr, flush=True)

            def _do_proxy(self):
                """Generic handler for all HTTP methods."""
                # Read body if present
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length) if content_length else b""

                # Build absolute URI for the relay
                url = self.path
                if not url.startswith("http://") and not url.startswith("https://"):
                    host = self.headers.get("Host", "")
                    url = f"http://{host}{self.path}"

                # Collect headers
                headers = {}
                for name in self.headers:
                    headers[name] = self.headers[name]

                # Forward over modem
                response = proxy._forward_request(self.command, url, headers, body)

                if response is None:
                    self.send_error(502, "Modem relay unreachable")
                    return

                # Send response back to browser
                self.send_response(response.status_code, response.status_message)
                for name, value in response.headers.items():
                    # Skip headers that http.server manages
                    if name.lower() in ("server", "date"):
                        continue
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(response.body)

            def do_CONNECT(self):
                """HTTPS tunneling — not yet supported."""
                self.send_error(501, "HTTPS CONNECT not implemented (Phase 2)")

            # Map all standard methods to the generic proxy handler
            do_GET = _do_proxy
            do_POST = _do_proxy
            do_HEAD = _do_proxy
            do_PUT = _do_proxy
            do_DELETE = _do_proxy
            do_PATCH = _do_proxy
            do_OPTIONS = _do_proxy

        return ProxyHandler

    def start(self) -> None:
        """Start the local proxy (blocking)."""
        handler_class = self._make_handler()
        self._http_server = _ThreadingHTTPServer(
            (self.config.listen_host, self.config.listen_port),
            handler_class,
        )

        print(f"Proxy listening on {self.config.listen_host}:{self.config.listen_port}",
              file=sys.stderr, flush=True)
        print(f"  Mode: {self.config.mode}", file=sys.stderr, flush=True)
        print(f"  Usage: curl --proxy http://{self.config.listen_host}:{self.config.listen_port}"
              f" http://example.com", file=sys.stderr, flush=True)

        self._http_server.serve_forever()

    def stop(self) -> None:
        """Stop the local proxy."""
        if self._http_server:
            self._http_server.shutdown()

        if self._session:
            self._session.close()
        if self._framer:
            self._framer.stop()
        if self._modem:
            self._modem.stop()


def main():
    """Entry point for modem-proxy command."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Modem HTTP Proxy — browse the web through an audio modem",
        epilog='Use "modem-audio devices" to list available audio devices.',
    )
    parser.add_argument(
        "--mode", choices=["acoustic", "cable", "loopback"],
        default=None,
        help="Audio mode (default: $MODEM_MODE or acoustic)",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="Proxy listen address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080,
                        help="Proxy listen port (default: 8080)")
    parser.add_argument("--audible", action="store_true",
                        help="Play audio even in loopback mode")
    parser.add_argument("-i", "--input-device", type=int, metavar="N",
                        help="Input device index")
    parser.add_argument("-o", "--output-device", type=int, metavar="N",
                        help="Output device index")
    args = parser.parse_args()

    config = ProxyConfig(
        listen_host=args.host,
        listen_port=args.port,
        mode=args.mode or "acoustic",
        input_device=args.input_device,
        output_device=args.output_device,
        audible=args.audible,
    )

    proxy = LocalProxy(config)
    try:
        proxy.start()
    except KeyboardInterrupt:
        print("\nShutting down proxy...", file=sys.stderr, flush=True)
        proxy.stop()


if __name__ == "__main__":
    main()
