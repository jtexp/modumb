"""HTTP client over modem transport.

Implements HTTP/1.1 client for Git smart HTTP protocol.
Uses Content-Length based framing.
"""

import re
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, field
from urllib.parse import urlparse, urljoin

from ..transport.session import Session


@dataclass
class HttpRequest:
    """HTTP request."""
    method: str
    path: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b''

    def encode(self) -> bytes:
        """Encode request to bytes."""
        # Request line
        lines = [f'{self.method} {self.path} HTTP/1.1']

        # Add Content-Length if body present
        if self.body:
            self.headers['Content-Length'] = str(len(self.body))

        # Headers
        for name, value in self.headers.items():
            lines.append(f'{name}: {value}')

        # Blank line before body
        lines.append('')
        lines.append('')

        header_bytes = '\r\n'.join(lines).encode()
        return header_bytes + self.body


@dataclass
class HttpResponse:
    """HTTP response."""
    status_code: int
    status_message: str
    headers: Dict[str, str]
    body: bytes

    @classmethod
    def decode(cls, data: bytes) -> Optional["HttpResponse"]:
        """Decode response from bytes."""
        # Find header/body separator
        sep = b'\r\n\r\n'
        sep_pos = data.find(sep)
        if sep_pos < 0:
            # Try LF only
            sep = b'\n\n'
            sep_pos = data.find(sep)
            if sep_pos < 0:
                return None

        header_data = data[:sep_pos].decode('utf-8', errors='replace')
        body = data[sep_pos + len(sep):]

        # Parse status line
        lines = header_data.split('\n')
        if not lines:
            return None

        status_line = lines[0].strip()
        match = re.match(r'HTTP/[\d.]+\s+(\d+)\s*(.*)', status_line)
        if not match:
            return None

        status_code = int(match.group(1))
        status_message = match.group(2)

        # Parse headers
        headers = {}
        for line in lines[1:]:
            line = line.strip()
            if ':' in line:
                name, value = line.split(':', 1)
                headers[name.strip().lower()] = value.strip()

        return cls(
            status_code=status_code,
            status_message=status_message,
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

    @property
    def content_type(self) -> Optional[str]:
        """Get Content-Type header value."""
        return self.headers.get('content-type')


class HttpClient:
    """HTTP client over session transport."""

    def __init__(
        self,
        session: Session,
        host: str = 'localhost',
        user_agent: str = 'modumb/0.1',
    ):
        """Initialize HTTP client.

        Args:
            session: Transport session
            host: Host header value
            user_agent: User-Agent header value
        """
        self.session = session
        self.host = host
        self.user_agent = user_agent

    def request(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        body: bytes = b'',
        timeout: float = 30.0,
    ) -> Optional[HttpResponse]:
        """Send HTTP request and receive response.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: Request path
            headers: Additional headers
            body: Request body
            timeout: Response timeout

        Returns:
            HTTP response, or None on error
        """
        # Build request
        req_headers = {
            'Host': self.host,
            'User-Agent': self.user_agent,
            'Connection': 'keep-alive',
        }
        if headers:
            req_headers.update(headers)

        request = HttpRequest(
            method=method,
            path=path,
            headers=req_headers,
            body=body,
        )

        # Send request
        request_bytes = request.encode()
        if not self.session.send(request_bytes):
            return None

        # Receive response
        response_data = self._receive_response(timeout)
        if not response_data:
            return None

        return HttpResponse.decode(response_data)

    def _receive_response(self, timeout: float) -> bytes:
        """Receive complete HTTP response."""
        data = bytearray()
        headers_complete = False
        content_length = None

        while True:
            chunk = self.session.receive(timeout=timeout)
            if chunk is None:
                break

            data.extend(chunk)

            # Check for header completion
            if not headers_complete:
                sep = b'\r\n\r\n'
                sep_pos = data.find(sep)
                if sep_pos < 0:
                    sep = b'\n\n'
                    sep_pos = data.find(sep)

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

            # Check if we have complete body
            if headers_complete and content_length is not None:
                sep_len = 4 if b'\r\n\r\n' in data else 2
                body_start = data.find(b'\r\n\r\n')
                if body_start < 0:
                    body_start = data.find(b'\n\n')
                    sep_len = 2

                if body_start >= 0:
                    body_start += sep_len
                    body_len = len(data) - body_start
                    if body_len >= content_length:
                        break

        return bytes(data)

    def get(
        self,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
    ) -> Optional[HttpResponse]:
        """Send GET request.

        Args:
            path: Request path
            headers: Additional headers
            timeout: Response timeout

        Returns:
            HTTP response
        """
        return self.request('GET', path, headers=headers, timeout=timeout)

    def post(
        self,
        path: str,
        body: bytes,
        headers: Optional[Dict[str, str]] = None,
        content_type: str = 'application/octet-stream',
        timeout: float = 30.0,
    ) -> Optional[HttpResponse]:
        """Send POST request.

        Args:
            path: Request path
            body: Request body
            headers: Additional headers
            content_type: Content-Type header
            timeout: Response timeout

        Returns:
            HTTP response
        """
        req_headers = {'Content-Type': content_type}
        if headers:
            req_headers.update(headers)

        return self.request('POST', path, headers=req_headers, body=body, timeout=timeout)
