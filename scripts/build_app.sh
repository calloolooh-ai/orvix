#!/bin/bash
#
# builds orvix.app, a real macOS app bundle, so orvix can run without a
# terminal and (the actual point) so macOS ties Accessibility + Input
# Monitoring permission to orvix.app itself rather than whatever terminal
# happened to launch `python -m orvix.gui`.
#
# usage: ./scripts/build_app.sh
# output: dist/orvix.app

set -euo pipefail

REPO="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

PYTHON="$REPO/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    echo "orvix: no venv at $PYTHON" >&2
    echo "  set one up first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

if ! "$PYTHON" -c "import py2app" 2>/dev/null; then
    echo "orvix: py2app isn't installed in the venv, installing it now (build-only dependency, not in requirements.txt)..."
    "$PYTHON" -m pip install -r requirements-build.txt
fi

echo "orvix: cleaning previous build output..."
rm -rf build dist

echo "orvix: building dist/orvix.app..."
"$PYTHON" setup.py py2app

echo
echo "orvix: built dist/orvix.app"
echo "  drag it to /Applications, or just: open dist/orvix.app"
echo "  first launch will prompt for Accessibility + Input Monitoring, same as the terminal flow (see docs/SETUP.md step 5)"
