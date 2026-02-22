"""Remote relay — Machine B (has internet).

Receives HTTP requests over modem, fetches from the real internet
via urllib, and returns the response back over modem.
"""

import os
import sys
import urllib.request
import urllib.error
from typing import Optional

from ..http.server import HttpServer, HttpServerRequest, HttpServerResponse, RequestHandler
from ..modem.modem import Modem
from ..modem.profiles import get_profile, AudioProfile
from .config import ProxyConfig


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

        # CONNECT method (HTTPS tunneling) — not yet supported
        if request.method == "CONNECT":
            return HttpServerResponse(
                status_code=501,
                status_message="Not Implemented",
                headers={"Content-Type": "text/plain"},
                body=b"HTTPS CONNECT tunneling not yet supported (Phase 2)",
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
        full_duplex = self.config.duplex == "full"
        self._server = HttpServer(modem, handler=handler, full_duplex=full_duplex)

        print(f"Modem relay starting (mode={self.config.mode}, baud={self.config.baud_rate}, duplex={self.config.duplex})",
              file=sys.stderr, flush=True)
        print("Waiting for modem connections...", file=sys.stderr, flush=True)

        self._server.serve_forever()

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
    parser.add_argument("--duplex", choices=["half", "full"],
                        default=os.environ.get("MODEM_DUPLEX", "half"),
                        help="Duplex mode (default: $MODEM_DUPLEX or half, full for cable/loopback)")
    args = parser.parse_args()

    mode = args.mode or os.environ.get("MODEM_MODE", "acoustic")
    if args.duplex == "full" and mode == "acoustic":
        print("ERROR: --duplex full requires --mode cable or loopback", file=sys.stderr)
        sys.exit(1)

    config = ProxyConfig(
        mode=mode,
        baud_rate=args.baud_rate,
        duplex=args.duplex,
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
