#!/bin/bash
# Run all BeoSound 5c unit tests (Python + JavaScript)
set -e
cd "$(dirname "$0")/.."

echo "=== Python unit tests ==="
python3 -m pytest tests/unit/python/ -v

echo ""
echo "=== JavaScript unit tests ==="
node --test tests/unit/js/test_*.js
