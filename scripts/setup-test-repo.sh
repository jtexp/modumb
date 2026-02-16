#!/bin/bash
# setup-test-repo.sh - Create a minimal test repository for acoustic modem testing
#
# Usage: ./scripts/setup-test-repo.sh [repo_path]
#
# Creates a small Git repository suitable for testing the acoustic modem transport.

set -e

REPO_PATH="${1:-/tmp/modem-test-repo}"

echo "=== Setting up test repository ==="
echo "Path: $REPO_PATH"
echo ""

# Clean up any existing repo
if [ -d "$REPO_PATH" ]; then
    echo "Removing existing repository..."
    rm -rf "$REPO_PATH"
fi

# Create repository
mkdir -p "$REPO_PATH"
cd "$REPO_PATH"

git init

# Create test content
cat > README.md << 'EOF'
# Acoustic Modem Test Repository

This repository was transmitted over sound waves using AFSK modulation.

- Frequency: 1200 Hz (mark) / 2200 Hz (space)
- Baud rate: 300 baud
- Transport: Git Smart HTTP over acoustic modem
EOF

cat > hello.txt << 'EOF'
Hello from the acoustic modem!
This file traveled through your speakers and microphone.
EOF

# Commit
git add .
git commit -m "Initial commit - test data for acoustic modem"

echo ""
echo "=== Test repository created ==="
echo "Path: $REPO_PATH"
echo "Size: $(du -sh "$REPO_PATH" | cut -f1)"
echo "Commits: $(git rev-list --count HEAD)"
echo ""
echo "To start the server:"
echo "  modem-git-server --loopback $REPO_PATH"
echo ""
echo "To clone (in another terminal):"
echo "  MODEM_LOOPBACK=1 git clone modem://audio/repo /tmp/clone-test"
