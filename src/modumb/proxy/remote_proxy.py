"""Remote relay — Machine B (has internet).

Receives HTTP requests over modem, fetches from the real internet
via urllib, and returns the response back over modem.
"""

import os
import select
import socket
import sys
import urllib.request
import urllib.error
from typing import Optional, Callable

from ..http.server import HttpServer, HttpServerRequest, HttpServerResponse, RequestHandler, ConnectHandler
from ..transport.session import Session
from ..modem.modem import Modem
from ..modem.profiles import get_profile, AudioProfile
from .config import ProxyConfig
from .tunnel import send_chunk, receive_chunk, send_close, MAX_TUNNEL_CHUNK


# Hop-by-hop headers that must not be forwarded (RFC 2616 §13.5.1)
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
})


def create_relay_handler(config: ProxyConfig) -> RequestHandler:
    """Create a request handler that relays HTTP requests to the internet.

    Args:
        config: Proxy configuration

    Returns:
        Request handler function for HttpServer
    """

    def handler(request: HttpServerRequest) -> HttpServerResponse:
        # Build the full URL from the request
        # The proxy client sends absolute URIs: GET http://example.com/path HTTP/1.1
        url = request.path
        if not url.startswith("http://") and not url.startswith("https://"):
            # Relative path — use Host header
            host = request.headers.get("host")
            if not host:
                return HttpServerResponse(
                    status_code=400,
                    status_message="Bad Request",
                    headers={"Content-Type": "text/plain"},
                    body=b"Missing Host header and no absolute URI",
                )
            url = f"http://{host}{request.path}"

        # CONNECT method (HTTPS tunneling)
        if request.method == "CONNECT":
            # Parse host:port for allowed_hosts check
            target = request.path
            host = target.split(':')[0] if ':' in target else target
            if config.allowed_hosts is not None and host not in config.allowed_hosts:
                return HttpServerResponse(
                    status_code=403,
                    status_message="Forbidden",
                    headers={"Content-Type": "text/plain"},
                    body=f"Host not allowed: {host}".encode(),
                )
            return HttpServerResponse(
                status_code=200,
                status_message="Connection Established",
                headers={},
                body=b"",
            )

        # Check allowed hosts
        if config.allowed_hosts is not None:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            if parsed.hostname not in config.allowed_hosts:
                return HttpServerResponse(
                    status_code=403,
                    status_message="Forbidden",
                    headers={"Content-Type": "text/plain"},
                    body=f"Host not allowed: {parsed.hostname}".encode(),
                )

        # Build upstream request
        headers = {}
        for name, value in request.headers.items():
            if name.lower() not in HOP_BY_HOP:
                headers[name] = value

        # Remove proxy-specific headers
        headers.pop("proxy-connection", None)

        try:
            req = urllib.request.Request(
                url,
                data=request.body if request.body else None,
                headers=headers,
                method=request.method,
            )
            print(f"RELAY: {request.method} {url}", file=sys.stderr, flush=True)

            with urllib.request.urlopen(req, timeout=config.request_timeout) as resp:
                body = resp.read(config.max_response_size)
                status_code = resp.status
                status_message = resp.reason

                # Collect response headers, stripping hop-by-hop
                resp_headers = {}
                for name, value in resp.getheaders():
                    if name.lower() not in HOP_BY_HOP:
                        resp_headers[name] = value

        except urllib.error.HTTPError as e:
            body = e.read(config.max_response_size)
            status_code = e.code
            status_message = e.reason
            resp_headers = {"Content-Type": e.headers.get("Content-Type", "text/plain")}

        except urllib.error.URLError as e:
            return HttpServerResponse(
                status_code=502,
                status_message="Bad Gateway",
                headers={"Content-Type": "text/plain"},
                body=f"Upstream error: {e.reason}".encode(),
            )

        except Exception as e:
            return HttpServerResponse(
                status_code=502,
                status_message="Bad Gateway",
                headers={"Content-Type": "text/plain"},
                body=f"Relay error: {e}".encode(),
            )

        print(f"RELAY: {status_code} {status_message} ({len(body)} bytes)",
              file=sys.stderr, flush=True)

        return HttpServerResponse(
            status_code=status_code,
            status_message=status_message,
            headers=resp_headers,
            body=body,
        )

    return handler


def create_connect_handler(config: ProxyConfig) -> ConnectHandler:
    """Create a CONNECT tunnel handler.

    Returns a function (session, target) -> None that:
    1. Opens TCP to upstream host:port
    2. Loops: receive_chunk from modem -> sendall to TCP -> recv from TCP -> send_chunk back
    3. Closes TCP on error/close signal
    """

    def handler(session: Session, target: str) -> None:
        # Parse host:port
        if ':' in target:
            host, port_str = target.rsplit(':', 1)
            try:
                port = int(port_str)
            except ValueError:
                port = 443
        else:
            host = target
            port = 443

        print(f"RELAY CONNECT: Opening TCP to {host}:{port}", file=sys.stderr, flush=True)

        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except Exception as e:
            print(f"RELAY CONNECT: TCP connect failed: {e}", file=sys.stderr, flush=True)
            # Send close signal so proxy knows tunnel failed
            send_close(session)
            return

        try:
            upstream.setblocking(False)
            initial_timeout = 2.0
            subsequent_timeout = 0.2

            while True:
                # Receive client data from modem
                client_data = receive_chunk(session, timeout=30.0)
                if client_data is None:
                    print("RELAY CONNECT: Modem receive timeout/error",
                          file=sys.stderr, flush=True)
                    break
                if client_data == b'':
                    print("RELAY CONNECT: Client sent close signal",
                          file=sys.stderr, flush=True)
                    break

                # Forward to upstream TCP
                try:
                    upstream.sendall(client_data)
                except Exception as e:
                    print(f"RELAY CONNECT: TCP send error: {e}",
                          file=sys.stderr, flush=True)
                    break

                # Read response from upstream (non-blocking with select)
                server_data = bytearray()
                read_timeout = initial_timeout
                while True:
                    ready, _, _ = select.select([upstream], [], [], read_timeout)
                    if not ready:
                        break
                    try:
                        chunk = upstream.recv(MAX_TUNNEL_CHUNK)
                    except (BlockingIOError, ConnectionError):
                        break
                    if not chunk:
                        # TCP closed by upstream
                        break
                    server_data.extend(chunk)
                    read_timeout = subsequent_timeout

                # Send server response back over modem
                if not send_chunk(session, bytes(server_data)):
                    print("RELAY CONNECT: Modem send failed",
                          file=sys.stderr, flush=True)
                    break

                # If upstream TCP closed (recv returned empty), we're done
                if ready and not chunk:
                    send_close(session)
                    break

        finally:
            upstream.close()
            print("RELAY CONNECT: Tunnel closed", file=sys.stderr, flush=True)

    return handler


class RemoteRelay:
    """Remote relay server — receives requests over modem, fetches from internet."""

    def __init__(self, config: Optional[ProxyConfig] = None):
        self.config = config or ProxyConfig()
        self._server: Optional[HttpServer] = None

    def _create_modem(self) -> Modem:
        """Create a Modem configured from our ProxyConfig."""
        profile = get_profile(self.config.mode)
        loopback = self.config.mode == "loopback"
        full_duplex = self.config.duplex == "full"
        return Modem(
            loopback=loopback,
            audible=self.config.audible,
            input_device=self.config.input_device,
            output_device=self.config.output_device,
            baud_rate=self.config.baud_rate,
            profile=profile,
            full_duplex=full_duplex,
        )

    def start(self) -> None:
        """Start the relay (blocking)."""
        modem = self._create_modem()
        handler = create_relay_handler(self.config)
        connect_handler = create_connect_handler(self.config)
        full_duplex = self.config.duplex == "full"
        self._server = HttpServer(modem, handler=handler, full_duplex=full_duplex,
                                  connect_handler=connect_handler)

        print(f"Modem relay starting (mode={self.config.mode}, baud={self.config.baud_rate}, duplex={self.config.duplex})",
              file=sys.stderr, flush=True)

        def _on_ready():
            print("Waiting for modem connections...", file=sys.stderr, flush=True)
            print("RELAY READY", file=sys.stderr, flush=True)

        self._server.serve_forever(on_ready=_on_ready)

    def stop(self) -> None:
        """Stop the relay."""
        if self._server:
            self._server.stop()


def main():
    """Entry point for modem-relay command."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Modem HTTP Relay — fetches web content for remote proxy clients",
        epilog='Use "modem-audio devices" to list available audio devices.',
    )
    parser.add_argument(
        "--mode", choices=["acoustic", "cable", "loopback"],
        default=None,
        help="Audio mode (default: $MODEM_MODE or acoustic)",
    )
    parser.add_argument("--audible", action="store_true",
                        help="Play audio even in loopback mode")
    parser.add_argument("--baud-rate", type=int, default=300,
                        help="Modem baud rate (default: 300)")
    parser.add_argument("-i", "--input-device", type=int, metavar="N",
                        help="Input device index")
    parser.add_argument("-o", "--output-device", type=int, metavar="N",
                        help="Output device index")
    parser.add_argument("--max-response-size", type=int, default=1_048_576,
                        metavar="BYTES", help="Max response body size (default: 1MB)")
    parser.add_argument("--allowed-hosts", nargs="*", metavar="HOST",
                        help="Only allow requests to these hosts")
    parser.add_argument("--duplex", choices=["half", "full"], default=None,
                        help="Duplex mode (default: full for cable/loopback, half for acoustic)")
    args = parser.parse_args()

    mode = args.mode or os.environ.get("MODEM_MODE", "acoustic")
    duplex = args.duplex or os.environ.get("MODEM_DUPLEX") or ("half" if mode == "acoustic" else "full")
    if duplex == "full" and mode == "acoustic":
        print("ERROR: --duplex full requires --mode cable or loopback", file=sys.stderr)
        sys.exit(1)

    config = ProxyConfig(
        mode=mode,
        baud_rate=args.baud_rate,
        duplex=duplex,
        input_device=args.input_device,
        output_device=args.output_device,
        audible=args.audible,
        max_response_size=args.max_response_size,
        allowed_hosts=args.allowed_hosts,
    )

    relay = RemoteRelay(config)
    try:
        relay.start()
    except KeyboardInterrupt:
        print("\nShutting down relay...", file=sys.stderr, flush=True)
        relay.stop()


if __name__ == "__main__":
    main()
