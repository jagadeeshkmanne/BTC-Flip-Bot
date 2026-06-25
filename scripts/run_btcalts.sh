#!/bin/bash
# BTC-led ALT basket (1h, long/short, vol-scaled, 1x) PAPER bot runner.
# Portable: resolves repo root from this script's own location; mkdir lock (macOS-safe).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
LOCK="/tmp/btcalts.lock"
if ! mkdir "$LOCK" 2>/dev/null; then exit 0; fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
cd "$REPO_DIR" || exit 0
PY="$(command -v python3 || echo /usr/bin/python3)"
"$PY" bot/bot_btcalts_1h.py 2>&1
