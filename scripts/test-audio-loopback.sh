#!/bin/bash
# test-audio-loopback.sh - Test audio transmission through the modem
#
# Usage: ./scripts/test-audio-loopback.sh [--audible]
#
# Tests the AFSK modem without full git transport:
# - Sends a test message
# - Receives via loopback
# - Verifies data integrity

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
AUDIBLE=""

if [ "$1" == "--audible" ]; then
    AUDIBLE="1"
    echo "Audible mode enabled - you will hear the modem sounds!"
fi

# Ensure we're in the right environment
if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
fi

cd "$PROJECT_DIR"

echo "========================================"
echo "  AFSK Modem Loopback Test"
echo "========================================"
echo ""

python3 << EOF
import sys
sys.path.insert(0, 'src')

from modumb.modem.modem import Modem
import time

print("Initializing modem in loopback mode...")
modem = Modem(loopback=True, audible=${AUDIBLE:-False})

print("Starting modem...")
modem.start()

# Test message
test_data = b'Hello from acoustic modem! Testing 1-2-3.'
print(f"\\nSending: {test_data.decode()}")
print(f"Length: {len(test_data)} bytes")
print(f"Expected duration: ~{len(test_data) * 8 / 300:.1f} seconds at 300 baud")
print("")

# Send
start_time = time.time()
modem.send(test_data)
send_time = time.time() - start_time

print(f"Transmission complete ({send_time:.1f}s)")
print("")

# Receive
print("Receiving...")
start_time = time.time()
received = modem.receive(timeout=5.0)
recv_time = time.time() - start_time

modem.stop()

if received:
    print(f"Received: {received}")
    print(f"Length: {len(received)} bytes ({recv_time:.1f}s)")
    print("")

    if received == test_data:
        print("========================================")
        print("  Test PASSED - Data matches!")
        print("========================================")
    else:
        print("========================================")
        print("  Test FAILED - Data mismatch!")
        print(f"  Expected: {test_data}")
        print(f"  Got:      {received}")
        print("========================================")
        sys.exit(1)
else:
    print("========================================")
    print("  Test FAILED - No data received!")
    print("========================================")
    sys.exit(1)
EOF
