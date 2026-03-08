#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# Source & Playback Regression Tests (wrapper)
#
# Copies the Python test script to the target device and runs it.
# Automatically discovers services and picks a playable source.
#
# Usage:
#   ./tests/integration/test-source-playback.sh
#   HOST=beosound5c-kitchen.local ./tests/integration/test-source-playback.sh
#   ./tests/integration/test-source-playback.sh --json
#
# Prerequisites:
#   - beo-router + beo-player-* + beo-http + beo-ui running on device
#   - At least one source with playable content (USB recommended)
# ─────────────────────────────────────────────────────────────────────

HOST="${HOST:-beosound5c-office.kirstenhome}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE_SCRIPT="/tmp/test-source-playback.py"

echo "═══════════════════════════════════════════════════"
echo " Device: $HOST"
echo "═══════════════════════════════════════════════════"

# Check connectivity
if ! ssh -o ConnectTimeout=3 "$HOST" "true" 2>/dev/null; then
    echo "ERROR: Cannot connect to $HOST"
    exit 2
fi

# Copy and run
scp -o ConnectTimeout=5 -q "$SCRIPT_DIR/test-source-playback.py" "$HOST:$REMOTE_SCRIPT"
ssh -o ConnectTimeout=5 "$HOST" "python3 $REMOTE_SCRIPT $*"
exit $?
