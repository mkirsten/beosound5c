#!/bin/bash
# Run all BeoSound 5c unit tests (Python + JavaScript)
#
#   tests/run-unit-tests.sh                          Python + JavaScript
#   tests/run-unit-tests.sh --python-only [args...]  Python only; args go to pytest
#
# The Python target list lives HERE and nowhere else. The git hooks and
# .github/workflows/tests.yml all call this script, because they used to keep
# their own copies of the list and drifted: tests/unit/spotify was in this
# script but in neither gate, so 31 tests gated nothing and a "green" hook
# meant less than it looked. Add a new test directory to PYTEST_TARGETS and
# every gate picks it up.
set -e
cd "$(dirname "$0")/.."

# Prefer the repo virtualenv when there is one. macOS ships an old system
# python3 that can't even import the services (PEP 604 annotations).
PYTHON="$PWD/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3

PYTEST_TARGETS=(tests/unit/python tests/unit/spotify)

PYTHON_ONLY=0
if [ "$1" = "--python-only" ]; then
    PYTHON_ONLY=1
    shift
fi

# Callers pass their own pytest flags (hooks want -x -q, CI wants --tb=short);
# a bare run is verbose as before.
PYTEST_ARGS=("$@")
[ ${#PYTEST_ARGS[@]} -eq 0 ] && PYTEST_ARGS=(-v)

echo "=== Python unit tests ==="
"$PYTHON" -m pytest "${PYTEST_TARGETS[@]}" "${PYTEST_ARGS[@]}"

if [ "$PYTHON_ONLY" -eq 1 ]; then
    exit 0
fi

echo ""
echo "=== JavaScript unit tests ==="
node --test tests/unit/js/test_*.js
