#!/usr/bin/env python3
"""End-to-end proxy test through two Virtual Audio Cables.

Tests the full pipeline:
  Browser/urllib -> LocalProxy(:8080) -> modem -> RemoteRelay -> internet -> response back

Requires two Virtual Audio Cable (Muzychenko) cables on Windows:
  - VAC Cable 1:  output=11 (Line Out) -> input=3 (Line 1)
  - VAC Cable 2:  output=8  (Line Out) -> input=5 (Line 2)

Device mapping:
  LocalProxy (Machine A)              RemoteRelay (Machine B)
    TX -> dev 11 (VAC1 Out)    -->    dev 3 (VAC1 In) -> RX
    RX <- dev 5  (VAC2 In)    <--    dev 8 (VAC2 Out) <- TX

Usage:
    .venv/Scripts/python.exe C:/Users/John/modumb/scripts/test_e2e_vac.py
"""
import sys
import os
import time
import socket
import subprocess
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PY = os.path.join(os.path.dirname(__file__), '..', '.venv', 'Scripts', 'python.exe')

# VAC Cable 1: LocalProxy TX -> RemoteRelay RX
VAC1_OUTPUT = 11  # Line Out (Virtual Cable 1)
VAC1_INPUT = 3    # Line 1 (Virtual Cable 1)

# VAC Cable 2: RemoteRelay TX -> LocalProxy RX
VAC2_OUTPUT = 8   # Line Out (Virtual Cable 2)
VAC2_INPUT = 5    # Line 2 (Virtual Cable 2)

PROXY_HOST = '127.0.0.1'
PROXY_PORT = 8080
MODE = 'cable'

# Test cases: (url, expected_content_substring, timeout_seconds)
TESTS = {
    'small': ('http://example.com', 'Example Domain', 120),
    'medium': ('http://info.cern.ch', 'http://info.cern.ch', 300),
}
DEFAULT_TEST = 'small'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def wait_for_port(host, port, timeout=30):
    """Block until a TCP port is accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False


def kill_proc(proc, name):
    """Terminate a subprocess, escalate to kill after 3s."""
    if proc and proc.poll() is None:
        print(f'Stopping {name} (pid={proc.pid})...', flush=True)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_test(test_url, expected_content, timeout, proxy_host, proxy_port):
    """Send a request through the proxy and check the result."""
    print(f'\nSending GET {test_url} through proxy...', flush=True)
    request_start = time.time()

    proxy_handler = urllib.request.ProxyHandler({
        'http': f'http://{proxy_host}:{proxy_port}',
    })
    opener = urllib.request.build_opener(proxy_handler)

    try:
        resp = opener.open(test_url, timeout=timeout)
        body = resp.read().decode('utf-8', errors='replace')
        status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        status = e.code
    except Exception as e:
        print(f'FAIL: Request failed: {e}', flush=True)
        return False

    request_time = time.time() - request_start
    print(f'Response: {status} ({len(body)} bytes, {request_time:.1f}s)', flush=True)
    bps = len(body) / request_time if request_time > 0 else 0
    print(f'Effective throughput: {bps:.1f} bytes/sec', flush=True)

    if status == 200 and expected_content in body:
        print(f'PASS: {test_url}', flush=True)
        return True
    else:
        print(f'FAIL: Unexpected response', flush=True)
        print(f'  Status: {status}', flush=True)
        print(f'  Contains "{expected_content}": {expected_content in body}', flush=True)
        print(f'  Body (first 500 chars): {body[:500]}', flush=True)
        return False


def main():
    # Parse optional test name from argv
    test_name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TEST
    if test_name not in TESTS:
        print(f'Unknown test: {test_name}. Available: {", ".join(TESTS)}', flush=True)
        return 1

    test_url, expected_content, timeout = TESTS[test_name]
    print(f'=== E2E Proxy Test: {test_name} ({test_url}) ===', flush=True)

    relay_proc = None
    proxy_proc = None
    start_time = time.time()

    try:
        # ---- Start RemoteRelay ----
        print(f'Starting relay (output={VAC2_OUTPUT}, input={VAC1_INPUT})...',
              flush=True)
        relay_cmd = [
            PY, '-m', 'modumb.proxy.remote_proxy',
            '--mode', MODE,
            '-o', str(VAC2_OUTPUT),
            '-i', str(VAC1_INPUT),
        ]
        relay_proc = subprocess.Popen(
            relay_cmd,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        time.sleep(3)
        if relay_proc.poll() is not None:
            print(f'FAIL: Relay exited early (code={relay_proc.returncode})', flush=True)
            return 1

        # ---- Start LocalProxy ----
        print(f'Starting proxy (output={VAC1_OUTPUT}, input={VAC2_INPUT}, '
              f'port={PROXY_PORT})...', flush=True)
        proxy_cmd = [
            PY, '-m', 'modumb.proxy.local_proxy',
            '--mode', MODE,
            '-o', str(VAC1_OUTPUT),
            '-i', str(VAC2_INPUT),
            '--port', str(PROXY_PORT),
        ]
        proxy_proc = subprocess.Popen(
            proxy_cmd,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

        # ---- Wait for proxy TCP port ----
        print('Waiting for proxy to be ready...', flush=True)
        if not wait_for_port(PROXY_HOST, PROXY_PORT, timeout=15):
            print('FAIL: Proxy port never became ready', flush=True)
            return 1
        print(f'Proxy is listening on {PROXY_HOST}:{PROXY_PORT}', flush=True)

        # ---- Run the test ----
        passed = run_test(test_url, expected_content, timeout,
                          PROXY_HOST, PROXY_PORT)

        total_time = time.time() - start_time
        print(f'\nTotal time: {total_time:.1f}s', flush=True)
        return 0 if passed else 1

    except KeyboardInterrupt:
        print('\nInterrupted by user', flush=True)
        return 130

    finally:
        kill_proc(proxy_proc, 'proxy')
        kill_proc(relay_proc, 'relay')


if __name__ == '__main__':
    sys.exit(main())
