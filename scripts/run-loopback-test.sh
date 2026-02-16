#!/bin/bash
# run-loopback-test.sh - End-to-end test of acoustic modem git transport
#
# Usage: ./scripts/run-loopback-test.sh
#
# This script performs a complete loopback test:
# 1. Creates a test repository
# 2. Starts the modem server
# 3. Clones via acoustic modem (loopback mode)
# 4. Verifies the clone
#
# Requires: modumb installed (pip install -e .)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TEST_REPO="/tmp/modem-test-repo"
CLONE_DIR="/tmp/modem-clone-test"

echo "========================================"
echo "  Acoustic Modem End-to-End Test"
echo "========================================"
echo ""

# Ensure we're in the right environment
if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
fi

# Check dependencies
if ! command -v modem-git-server &> /dev/null; then
    echo "Error: modem-git-server not found"
    echo "Run: pip install -e . && export PATH=\"$PROJECT_DIR/bin:\$PATH\""
    exit 1
fi

# Step 1: Create test repository
echo "[1/5] Creating test repository..."
"$SCRIPT_DIR/setup-test-repo.sh" "$TEST_REPO"

# Step 2: Clean up any previous clone
echo ""
echo "[2/5] Preparing clone destination..."
rm -rf "$CLONE_DIR"

# Step 3: Start server in background
echo ""
echo "[3/5] Starting modem server (loopback mode)..."
modem-git-server --loopback "$TEST_REPO" &
SERVER_PID=$!

# Give server time to start
sleep 2

# Ensure server gets cleaned up
cleanup() {
    echo ""
    echo "Cleaning up..."
    kill $SERVER_PID 2>/dev/null || true
    wait $SERVER_PID 2>/dev/null || true
}
trap cleanup EXIT

# Step 4: Clone via acoustic modem
echo ""
echo "[4/5] Cloning via acoustic modem..."
echo "      (This will take some time at 300 baud)"
echo ""

MODEM_LOOPBACK=1 git clone modem://audio/repo "$CLONE_DIR"

# Step 5: Verify
echo ""
echo "[5/5] Verifying clone..."
echo ""

if [ -f "$CLONE_DIR/README.md" ]; then
    echo "=== Clone successful! ==="
    echo ""
    echo "Source repository: $TEST_REPO"
    echo "Cloned to: $CLONE_DIR"
    echo ""
    echo "Contents:"
    ls -la "$CLONE_DIR"
    echo ""
    echo "README.md:"
    cat "$CLONE_DIR/README.md"
    echo ""
    echo "========================================"
    echo "  Test PASSED"
    echo "========================================"
else
    echo "=== Clone FAILED ==="
    echo "Expected file not found: $CLONE_DIR/README.md"
    exit 1
fi
