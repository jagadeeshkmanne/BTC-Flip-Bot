#!/bin/bash
# self_heal.sh — runs every 5 min via the bybit-selfheal systemd timer.
#
# systemd's Restart=always only catches a process that EXITS. A single-
# threaded HTTP server can hang while still "active". This checks the
# dashboard actually RESPONDS, and restarts it if not. Also re-starts the
# trading timer if it somehow got disabled.
set -u
BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$BOT_DIR/data/self_heal.log"
ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

# ── Dashboard server — must actually answer, not just be "active" ──
code=$(curl -s -o /dev/null -w "%{http_code}" -m 8 http://localhost:8889/api/health 2>/dev/null)
if [ "$code" != "200" ]; then
    echo "$(ts) dashboard not responding (HTTP ${code:-000}) — restarting server" >> "$LOG"
    systemctl restart bybit-divflip-server.service
    sleep 5
    code2=$(curl -s -o /dev/null -w "%{http_code}" -m 8 http://localhost:8889/api/health 2>/dev/null)
    echo "$(ts) after restart: HTTP ${code2:-000}" >> "$LOG"
fi

# ── Trading timer — make sure it is still scheduled ──
if ! systemctl is-active --quiet bybit-divflip.timer; then
    echo "$(ts) bybit-divflip.timer inactive — starting it" >> "$LOG"
    systemctl start bybit-divflip.timer
fi
