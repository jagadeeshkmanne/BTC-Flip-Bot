#!/bin/bash
# self_heal.sh — Runs every 10 minutes via cron
# Checks: (1) dashboard server alive, (2) bot cron fired recently
# Restarts the dashboard server if down; logs an alert if bot is stale

set -u
BOT_DIR="/home/jags/BTC-Flip-Bot"
LOG_FILE="$BOT_DIR/data/self_heal.log"
DATA_DIR="$BOT_DIR/data/testnet"
SERVER_PID_FILE="$BOT_DIR/data/server.pid"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "$(ts) $*" >> "$LOG_FILE"; }

# ── Check 1: Dashboard server responding ──────────────────────────
SERVER_OK=0
if curl -s -o /dev/null -w "%{http_code}" -m 5 http://localhost:8888/dashboard.html 2>/dev/null | grep -q "^200$"; then
    SERVER_OK=1
fi

if [ "$SERVER_OK" -ne 1 ]; then
    log "Server DOWN — attempting restart"
    # Kill any stuck server processes
    pkill -f "$BOT_DIR/server.py" 2>/dev/null
    sleep 2
    # Start fresh
    cd "$BOT_DIR" || { log "ERR: cannot cd to $BOT_DIR"; exit 1; }
    nohup /usr/bin/python3 "$BOT_DIR/server.py" > "$BOT_DIR/data/server.log" 2>&1 &
    echo $! > "$SERVER_PID_FILE"
    disown
    sleep 3
    # Verify
    if curl -s -o /dev/null -w "%{http_code}" -m 5 http://localhost:8888/dashboard.html 2>/dev/null | grep -q "^200$"; then
        log "Server restart OK (pid $(cat $SERVER_PID_FILE 2>/dev/null))"
    else
        log "Server restart FAILED"
    fi
fi

# ── Check 2: Bot cron fired in last 15 min ────────────────────────
BOT_LOG="$BOT_DIR/data/swing_cron.log"
if [ -f "$BOT_LOG" ]; then
    LAST_MOD=$(stat -c %Y "$BOT_LOG" 2>/dev/null || stat -f %m "$BOT_LOG" 2>/dev/null)
    NOW=$(date +%s)
    AGE=$((NOW - LAST_MOD))
    if [ "$AGE" -gt 900 ]; then  # 15 min
        log "WARN: bot cron log stale ($((AGE/60)) min old) — check crontab"
    fi
fi

# ── Check 3: Position sanity ──────────────────────────────────────
# If state.json has a position but the bot hasn't ran recently, flag it
if [ -f "$DATA_DIR/state.json" ] && [ -f "$BOT_LOG" ]; then
    HAS_POS=$(grep -c '"side":' "$DATA_DIR/state.json" 2>/dev/null || echo 0)
    if [ "$HAS_POS" -gt 0 ] && [ "${AGE:-0}" -gt 900 ]; then
        log "ALERT: open position but bot stale >15min"
    fi
fi

exit 0
