"""Git remote helper for modem:// protocol.

This implements the git remote helper protocol for the modem:// URL scheme.
Git will call this as an external command when accessing modem:// URLs.

Usage:
    git clone modem://audio/path/to/repo
    git fetch modem://audio/path/to/repo

The helper communicates with git via stdin/stdout using a simple text protocol.

References:
- https://git-scm.com/docs/gitremote-helpers
"""

import os
import sys
import tempfile
import subprocess
from typing import Optional, TextIO
from urllib.parse import urlparse

from ..modem.modem import Modem
from ..datalink.framer import Framer
from ..transport.session import SessionManager
from ..transport.reliable import ReliableTransport
from ..http.client import HttpClient
from .smart_http import GitSmartHttpClient


class GitRemoteHelper:
    """Git remote helper implementation for modem:// URLs."""

    def __init__(
        self,
        remote_name: str,
        remote_url: str,
        stdin: TextIO = sys.stdin,
        stdout: TextIO = sys.stdout,
        stderr: TextIO = sys.stderr,
    ):
        """Initialize remote helper.

        Args:
            remote_name: Name of the remote (e.g., 'origin')
            remote_url: URL of the remote (modem://audio/repo)
            stdin: Input stream (from git)
            stdout: Output stream (to git)
            stderr: Error output stream
        """
        self.remote_name = remote_name
        self.remote_url = remote_url
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr

        # Parse URL
        parsed = urlparse(remote_url)
        self.scheme = parsed.scheme  # 'modem'
        self.host = parsed.netloc    # 'audio' or device name
        self.path = parsed.path      # '/path/to/repo'

        # Connection state
        self.modem: Optional[Modem] = None
        self.framer: Optional[Framer] = None
        self.session_manager: Optional[SessionManager] = None
        self.http_client: Optional[HttpClient] = None
        self.git_client: Optional[GitSmartHttpClient] = None

        # Use loopback only if MODEM_LOOPBACK is set or host is 'loopback'
        # 'audio' host uses real audio through speakers/mic
        self.use_loopback = (
            self.host == 'loopback' or
            os.environ.get('MODEM_LOOPBACK', '').lower() in ('1', 'true', 'yes')
        )

    def log(self, message: str) -> None:
        """Log message to stderr."""
        print(f'modem: {message}', file=self.stderr, flush=True)

    def send(self, line: str) -> None:
        """Send line to git."""
        print(line, file=self.stdout, flush=True)

    def readline(self) -> str:
        """Read line from git."""
        line = self.stdin.readline()
        return line.strip() if line else ''

    def connect(self) -> bool:
        """Establish modem connection.

        Returns:
            True if connection established
        """
        try:
            self.log(f'Connecting via modem (loopback={self.use_loopback})...')

            # Create modem
            self.modem = Modem(loopback=self.use_loopback)
            self.modem.start()

            # Create framer
            self.framer = Framer(self.modem)
            self.framer.start()

            # Create session manager
            self.session_manager = SessionManager(self.framer)

            # Create client session
            session = self.session_manager.create_client_session()
            if session is None:
                self.log('Failed to establish session')
                return False

            # Create HTTP client
            self.http_client = HttpClient(session, host=self.host)

            # Create Git client
            self.git_client = GitSmartHttpClient(self.http_client, repo_path=self.path)

            self.log('Connected!')
            return True

        except Exception as e:
            self.log(f'Connection failed: {e}')
            return False

    def disconnect(self) -> None:
        """Close modem connection."""
        if self.session_manager:
            self.session_manager.close_all()

        if self.framer:
            self.framer.stop()

        if self.modem:
            self.modem.stop()

    def cmd_capabilities(self) -> None:
        """Handle 'capabilities' command."""
        # Report our capabilities
        self.send('fetch')
        self.send('option')
        self.send('')  # End of capabilities

    def cmd_list(self, for_push: bool = False) -> None:
        """Handle 'list' command - list refs."""
        if not self.connect():
            self.send('')
            return

        try:
            refs = self.git_client.discover_refs()

            for name, sha1 in refs.items():
                if name == 'HEAD':
                    # HEAD as symbolic ref
                    self.send(f'@refs/heads/master HEAD')
                else:
                    self.send(f'{sha1} {name}')

        except Exception as e:
            self.log(f'Failed to list refs: {e}')

        self.send('')  # End of list

    def cmd_fetch(self, sha1: str, name: str) -> None:
        """Handle 'fetch' command - fetch objects."""
        # Collect all fetch commands first
        fetches = [(sha1, name)]

        while True:
            line = self.readline()
            if not line or line == '':
                break
            if line.startswith('fetch '):
                parts = line.split(' ', 2)
                if len(parts) >= 3:
                    fetches.append((parts[1], parts[2]))

        self.log(f'Fetching {len(fetches)} refs...')

        try:
            # Get all refs we want
            want_refs = [sha1 for sha1, name in fetches]

            # Fetch pack
            pack_data = self.git_client.fetch_pack(want_refs)

            if pack_data:
                # Write pack to temp file and index it
                with tempfile.NamedTemporaryFile(
                    prefix='modem_pack_',
                    suffix='.pack',
                    delete=False
                ) as f:
                    f.write(pack_data)
                    pack_path = f.name

                try:
                    # Index the pack using git
                    subprocess.run(
                        ['git', 'index-pack', '--stdin'],
                        input=pack_data,
                        capture_output=True,
                        check=True,
                    )
                    self.log(f'Indexed pack ({len(pack_data)} bytes)')
                except subprocess.CalledProcessError as e:
                    self.log(f'Failed to index pack: {e}')
                finally:
                    # Clean up temp file
                    try:
                        os.unlink(pack_path)
                    except OSError:
                        pass

        except Exception as e:
            self.log(f'Fetch failed: {e}')

        self.send('')  # End of fetch

    def cmd_option(self, name: str, value: str) -> None:
        """Handle 'option' command."""
        # We don't support any options currently
        self.send('unsupported')

    def run(self) -> int:
        """Run the remote helper main loop.

        Returns:
            Exit code
        """
        try:
            while True:
                line = self.readline()
                if not line:
                    break

                self.log(f'Command: {line}')

                if line == 'capabilities':
                    self.cmd_capabilities()

                elif line == 'list':
                    self.cmd_list(for_push=False)

                elif line == 'list for-push':
                    self.cmd_list(for_push=True)

                elif line.startswith('fetch '):
                    parts = line.split(' ', 2)
                    if len(parts) >= 3:
                        self.cmd_fetch(parts[1], parts[2])
                    else:
                        self.send('')

                elif line.startswith('option '):
                    parts = line.split(' ', 2)
                    if len(parts) >= 3:
                        self.cmd_option(parts[1], parts[2])
                    else:
                        self.send('unsupported')

                elif line == '':
                    # Empty line ends batch
                    continue

                else:
                    self.log(f'Unknown command: {line}')
                    self.send('')

        except Exception as e:
            self.log(f'Error: {e}')
            return 1

        finally:
            self.disconnect()

        return 0


def main():
    """Main entry point for git-remote-modem."""
    if len(sys.argv) < 3:
        print('Usage: git-remote-modem <remote-name> <url>', file=sys.stderr)
        sys.exit(1)

    remote_name = sys.argv[1]
    remote_url = sys.argv[2]

    helper = GitRemoteHelper(remote_name, remote_url)
    sys.exit(helper.run())


if __name__ == '__main__':
    main()
