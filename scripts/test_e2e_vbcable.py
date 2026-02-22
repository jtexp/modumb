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
    .venv/Scripts/python.exe C:/Users/John/modumb/scripts/test_e2e_vbcable.py
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
TIMEOUT = 120  # seconds total

TEST_URL = 'http://example.com'
EXPECTED_CONTENT = 'Example Domain'

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

def main():
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
        # Give relay time to open audio devices and start listening
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

        # ---- Send test request through proxy ----
        print(f'Sending GET {TEST_URL} through proxy...', flush=True)
        request_start = time.time()

        proxy_handler = urllib.request.ProxyHandler({
            'http': f'http://{PROXY_HOST}:{PROXY_PORT}',
        })
        opener = urllib.request.build_opener(proxy_handler)

        try:
            resp = opener.open(TEST_URL, timeout=TIMEOUT)
            body = resp.read().decode('utf-8', errors='replace')
            status = resp.status
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            status = e.code
        except Exception as e:
            print(f'FAIL: Request failed: {e}', flush=True)
            return 1

        request_time = time.time() - request_start
        total_time = time.time() - start_time

        # ---- Check result ----
        print(f'Response: {status} ({len(body)} bytes, {request_time:.1f}s)', flush=True)

        if status == 200 and EXPECTED_CONTENT in body:
            print(f'\nPASS: End-to-end proxy test through VB-Cable succeeded!', flush=True)
            print(f'Total time: {total_time:.1f}s', flush=True)
            return 0
        else:
            print(f'\nFAIL: Unexpected response', flush=True)
            print(f'  Status: {status}', flush=True)
            print(f'  Contains "{EXPECTED_CONTENT}": {EXPECTED_CONTENT in body}', flush=True)
            print(f'  Body (first 500 chars): {body[:500]}', flush=True)
            return 1

    except KeyboardInterrupt:
        print('\nInterrupted by user', flush=True)
        return 130

    finally:
        kill_proc(proxy_proc, 'proxy')
        kill_proc(relay_proc, 'relay')


if __name__ == '__main__':
    sys.exit(main())
