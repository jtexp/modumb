"""Git Smart HTTP protocol implementation.

Implements:
- /info/refs?service=git-upload-pack (discover refs)
- /git-upload-pack (fetch objects)
- /git-receive-pack (push objects) - deferred

References:
- https://git-scm.com/docs/http-protocol
- https://git-scm.com/docs/protocol-v2
"""

import subprocess
import os
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass

from ..http.client import HttpClient, HttpResponse
from ..http.server import HttpServerRequest, HttpServerResponse, RequestHandler
from ..http.pktline import PktLine


# Content types for Git HTTP protocol
GIT_UPLOAD_PACK_ADVERTISEMENT = 'application/x-git-upload-pack-advertisement'
GIT_UPLOAD_PACK_REQUEST = 'application/x-git-upload-pack-request'
GIT_UPLOAD_PACK_RESULT = 'application/x-git-upload-pack-result'
GIT_RECEIVE_PACK_ADVERTISEMENT = 'application/x-git-receive-pack-advertisement'


@dataclass
class GitRef:
    """A git reference (branch/tag/HEAD)."""
    name: str
    sha1: str
    peeled: Optional[str] = None  # For tags


class GitSmartHttpClient:
    """Client for Git Smart HTTP protocol."""

    def __init__(self, http_client: HttpClient, repo_path: str = '/'):
        """Initialize Git HTTP client.

        Args:
            http_client: HTTP client for communication
            repo_path: Base path to repository on server
        """
        self.http = http_client
        self.repo_path = repo_path.rstrip('/')
        self.capabilities: set[str] = set()
        self.refs: Dict[str, str] = {}

    def _make_path(self, endpoint: str) -> str:
        """Build full path for endpoint."""
        return f'{self.repo_path}/{endpoint}'

    def discover_refs(self) -> Dict[str, str]:
        """Discover refs via info/refs endpoint.

        Returns:
            Dict mapping ref names to SHA1 hashes
        """
        path = self._make_path('info/refs')
        params = '?service=git-upload-pack'

        response = self.http.get(path + params)
        if response is None or response.status_code != 200:
            raise RuntimeError(f'Failed to discover refs: {response}')

        # Parse response
        result = PktLine.parse_capability_advertisement(response.body)
        self.refs = result['refs']
        self.capabilities = result['capabilities']

        return self.refs

    def fetch_pack(self, want_refs: List[str], have_refs: Optional[List[str]] = None) -> bytes:
        """Fetch pack file for wanted refs.

        Args:
            want_refs: SHA1 hashes of objects to fetch
            have_refs: SHA1 hashes of objects we already have

        Returns:
            Pack file data
        """
        if not want_refs:
            return b''

        # Build capabilities we support
        caps = ['multi_ack', 'side-band-64k', 'ofs-delta', 'thin-pack']
        caps = [c for c in caps if c in self.capabilities]

        # Build request
        lines = []
        for i, sha1 in enumerate(want_refs):
            line = f'want {sha1}'
            if i == 0 and caps:
                line += ' ' + ' '.join(caps)
            lines.append((line + '\n').encode())

        # Add flush
        request_body = PktLine.encode_lines(lines, flush=True)

        # Add 'have' lines if we have existing objects
        if have_refs:
            have_lines = [(f'have {sha1}\n').encode() for sha1 in have_refs]
            request_body += PktLine.encode_lines(have_lines, flush=True)

        # Add done
        request_body += PktLine.encode_line(b'done\n')

        # Send request
        path = self._make_path('git-upload-pack')
        response = self.http.post(
            path,
            body=request_body,
            content_type=GIT_UPLOAD_PACK_REQUEST,
        )

        if response is None or response.status_code != 200:
            raise RuntimeError(f'Failed to fetch pack: {response}')

        # Parse response - may have NAK/ACK followed by pack data
        body = response.body

        # Skip pkt-line responses until we hit pack data
        while body and not body.startswith(b'PACK'):
            if len(body) < 4:
                break

            try:
                length = int(body[:4], 16)
                if length == 0:
                    body = body[4:]
                elif length >= 4:
                    body = body[length:]
                else:
                    break
            except ValueError:
                break

        return body

    def clone(self) -> tuple[Dict[str, str], bytes]:
        """Perform a clone operation.

        Returns:
            Tuple of (refs dict, pack data)
        """
        # Discover refs
        refs = self.discover_refs()

        if not refs:
            return {}, b''

        # Want HEAD (or first ref)
        want = []
        if 'HEAD' in refs:
            want.append(refs['HEAD'])
        else:
            # Get first ref
            want.append(next(iter(refs.values())))

        # Fetch pack
        pack_data = self.fetch_pack(want)

        return refs, pack_data


class GitSmartHttpServer:
    """Server for Git Smart HTTP protocol."""

    def __init__(self, repo_path: str):
        """Initialize Git HTTP server.

        Args:
            repo_path: Path to git repository
        """
        self.repo_path = os.path.abspath(repo_path)

        # Verify it's a git repo
        git_dir = os.path.join(self.repo_path, '.git')
        if os.path.isdir(git_dir):
            self.git_dir = git_dir
        elif os.path.isfile(os.path.join(self.repo_path, 'HEAD')):
            # Bare repository
            self.git_dir = self.repo_path
        else:
            raise ValueError(f'Not a git repository: {repo_path}')

    def handle_info_refs(self, service: str) -> HttpServerResponse:
        """Handle /info/refs endpoint.

        Args:
            service: git-upload-pack or git-receive-pack

        Returns:
            HTTP response with ref advertisement
        """
        if service not in ('git-upload-pack', 'git-receive-pack'):
            return HttpServerResponse.not_found(f'Unknown service: {service}')

        # Call git to get ref advertisement
        try:
            result = subprocess.run(
                [service, '--stateless-rpc', '--advertise-refs', self.repo_path],
                capture_output=True,
                timeout=30,
            )
        except Exception as e:
            return HttpServerResponse.error(str(e))

        if result.returncode != 0:
            return HttpServerResponse.error(result.stderr.decode())

        # Build response with service header
        body = PktLine.encode_line(f'# service={service}\n'.encode())
        body += PktLine.encode_flush()
        body += result.stdout

        content_type = (
            GIT_UPLOAD_PACK_ADVERTISEMENT if service == 'git-upload-pack'
            else GIT_RECEIVE_PACK_ADVERTISEMENT
        )

        return HttpServerResponse(
            status_code=200,
            status_message='OK',
            headers={
                'Content-Type': content_type,
                'Cache-Control': 'no-cache',
            },
            body=body,
        )

    def handle_upload_pack(self, request_body: bytes) -> HttpServerResponse:
        """Handle /git-upload-pack endpoint.

        Args:
            request_body: Request body with wants/haves

        Returns:
            HTTP response with pack data
        """
        try:
            # Call git-upload-pack with the request
            result = subprocess.run(
                ['git-upload-pack', '--stateless-rpc', self.repo_path],
                input=request_body,
                capture_output=True,
                timeout=300,  # Pack generation can take time
            )
        except Exception as e:
            return HttpServerResponse.error(str(e))

        if result.returncode != 0:
            return HttpServerResponse.error(result.stderr.decode())

        return HttpServerResponse(
            status_code=200,
            status_message='OK',
            headers={
                'Content-Type': GIT_UPLOAD_PACK_RESULT,
                'Cache-Control': 'no-cache',
            },
            body=result.stdout,
        )

    def handle_receive_pack(self, request_body: bytes) -> HttpServerResponse:
        """Handle /git-receive-pack endpoint (push).

        Args:
            request_body: Request body with pack data

        Returns:
            HTTP response
        """
        # Deferred for PoC
        return HttpServerResponse.error('Push not implemented')

    def handle_request(self, request: HttpServerRequest) -> HttpServerResponse:
        """Handle incoming HTTP request.

        Args:
            request: HTTP request

        Returns:
            HTTP response
        """
        path = request.path

        # Handle /info/refs
        if '/info/refs' in path:
            # Extract service from query string
            if '?' in path:
                query = path.split('?', 1)[1]
                params = dict(p.split('=') for p in query.split('&') if '=' in p)
                service = params.get('service', '')
            else:
                service = ''

            return self.handle_info_refs(service)

        # Handle /git-upload-pack
        if path.endswith('/git-upload-pack'):
            return self.handle_upload_pack(request.body)

        # Handle /git-receive-pack
        if path.endswith('/git-receive-pack'):
            return self.handle_receive_pack(request.body)

        return HttpServerResponse.not_found()


def create_server_handler(repo_path: str) -> RequestHandler:
    """Create HTTP request handler for git server.

    Args:
        repo_path: Path to git repository

    Returns:
        Request handler function
    """
    server = GitSmartHttpServer(repo_path)
    return server.handle_request
