#!/bin/bash
# All-Weather 4-coin basket (4h perp, long/short reverse, 1x) PAPER bot runner.
# Portable: resolves repo root from this script's own location (works on macOS dev
# box and the Linux prod box alike). Uses a mkdir lock since macOS has no flock.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
LOCK="/tmp/allweather.lock"

# mkdir is atomic — acts as a mutex. Bail (exit 0) if a run is already in flight.
if ! mkdir "$LOCK" 2>/dev/null; then
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

cd "$REPO_DIR" || exit 0
PY="$(command -v python3 || echo /usr/bin/python3)"
"$PY" bot/bot_allweather_4h.py 2>&1
