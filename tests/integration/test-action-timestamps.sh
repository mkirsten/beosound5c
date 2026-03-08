#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Action Timestamp Race Prevention Tests
#
# Usage:
#   ./tests/integration/test-action-timestamps.sh              # all suites
#   ./tests/integration/test-action-timestamps.sh common       # common only
#   ./tests/integration/test-action-timestamps.sh local        # local player
#   ./tests/integration/test-action-timestamps.sh sonos        # Sonos player
#   HOST=beosound5c-kitchen.local ./tests/integration/test-action-timestamps.sh sonos
# ─────────────────────────────────────────────────────────────────

HOST="${HOST:-beosound5c-office.kirstenhome}"
SUITE="${1:-all}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "═══════════════════════════════════════════════════"
echo " Device: $HOST    Suite: $SUITE"
echo "═══════════════════════════════════════════════════"

if ! ssh -o ConnectTimeout=3 "$HOST" "true" 2>/dev/null; then
    echo "ERROR: Cannot connect to $HOST"
    exit 2
fi

# Copy helpers + test files
scp -o ConnectTimeout=5 -q \
    "$SCRIPT_DIR/test-action-ts-helpers.py" "$HOST:/tmp/helpers.py"
scp -o ConnectTimeout=5 -q \
    "$SCRIPT_DIR/test-action-ts-common.py" \
    "$SCRIPT_DIR/test-action-ts-local.py" \
    "$SCRIPT_DIR/test-action-ts-sonos.py" \
    "$HOST:/tmp/"

EXIT=0

run_suite() {
    local name="$1" file="$2"
    echo ""
    ssh -o ConnectTimeout=5 "$HOST" "python3 /tmp/$file"
    local rc=$?
    if [ $rc -ne 0 ]; then EXIT=$rc; fi
}

case "$SUITE" in
    common) run_suite "common" "test-action-ts-common.py" ;;
    local)  run_suite "local"  "test-action-ts-local.py" ;;
    sonos)  run_suite "sonos"  "test-action-ts-sonos.py" ;;
    all)
        run_suite "common" "test-action-ts-common.py"
        run_suite "local"  "test-action-ts-local.py"
        ;;
    *)
        echo "Unknown suite: $SUITE (use common, local, sonos, or all)"
        exit 2
        ;;
esac

exit $EXIT
