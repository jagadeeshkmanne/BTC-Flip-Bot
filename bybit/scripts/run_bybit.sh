#!/bin/bash
# Runner for the Bybit divflip v1 LIVE bot.
# Called by the systemd timer (bybit-divflip.timer) every 1 minute.
cd "$(dirname "$0")/.." || exit 1
exec /usr/bin/python3 bot_divflip_bybit.py 2>&1
