#!/bin/bash
# self_heal.sh — Runs every 10 minutes via cron
# Checks: (1) dashboard server alive, (2) all three paper bots fired recently
# Restarts the dashboard server if down; logs an alert if any bot is stale

set -u
BOT_DIR="/home/jags/BTC-Flip-Bot"
LOG_FILE="$BOT_DIR/data/self_heal.log"
# 2026-06-10: fleet is v2.1 + v2.2 (counter-trend 5×). With-trend + 3× bots removed.
V21_DIR="$BOT_DIR/data/v2.1"
V22_DIR="$BOT_DIR/data/v2.2"
SERVER_PID_FILE="$BOT_DIR/data/server.pid"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "$(ts) $*" >> "$LOG_FILE"; }

# ── Check 1: Dashboard server responding ──────────────────────────
# 2026-06-05: health-check via /api/bots/all (returns 200 JSON), not
# /dashboard.html (which now returns 302 redirect to /bots/v2).
# Try 3 times with 10s timeout each — server can be transiently slow under
# parallel load, false-positive restarts cause more issues than they solve.
SERVER_OK=0
for attempt in 1 2 3; do
    if curl -s -o /dev/null -w "%{http_code}" -m 10 http://localhost:8888/api/bots/all 2>/dev/null | grep -q "^200$"; then
        SERVER_OK=1
        break
    fi
    sleep 2
done

if [ "$SERVER_OK" -ne 1 ]; then
    log "Server DOWN after 3 attempts — attempting restart via systemctl"
    # Stop service cleanly (systemctl waits up to 30s for graceful exit)
    sudo systemctl stop btc-bot-server 2>&1 | head -1 >> "$LOG_FILE"
    sleep 3
    # If port still bound, kill any stale process matching exact path
    if sudo ss -tlnp 2>/dev/null | grep -q ":8888"; then
        log "Port 8888 still bound — killing zombie processes"
        sudo pkill -9 -f "/home/jags/BTC-Flip-Bot/dashboard/server.py" 2>/dev/null
        sleep 2
    fi
    # Start fresh via systemctl (consistent with how it was deployed)
    sudo systemctl start btc-bot-server 2>&1 | head -1 >> "$LOG_FILE"
    sleep 4
    # Verify
    if curl -s -o /dev/null -w "%{http_code}" -m 10 http://localhost:8888/api/bots/all 2>/dev/null | grep -q "^200$"; then
        log "Server restart OK"
    else
        log "Server restart FAILED — manual intervention needed"
    fi
fi

# ── check_bot: log staleness + open-position alert for a paper bot ──
# Args: $1=label  $2=log_path  $3=state_path
check_bot() {
    local label="$1"
    local bot_log="$2"
    local state_file="$3"
    local age=99999
    if [ -f "$bot_log" ]; then
        local last_mod
        last_mod=$(stat -c %Y "$bot_log" 2>/dev/null || stat -f %m "$bot_log" 2>/dev/null)
        local now
        now=$(date +%s)
        age=$((now - last_mod))
        if [ "$age" -gt 900 ]; then  # 15 min
            log "WARN: $label bot log stale ($((age/60)) min old) — check crontab"
        fi
    else
        log "WARN: $label bot log missing: $bot_log"
    fi
    # Open-position alert. awk's c+0 returns 0 when no matches (avoids the
    # "grep -c || echo 0" trap where grep prints 0 on no-match exit 1, then
    # echo 0 prints another → "0\n0" breaks integer compare).
    if [ -f "$state_file" ]; then
        local has_pos
        has_pos=$(awk '/"side":/{c++} END{print c+0}' "$state_file" 2>/dev/null)
        : "${has_pos:=0}"
        if [ "$has_pos" -gt 0 ] && [ "$age" -gt 900 ]; then
            log "ALERT: $label bot has open position but cron stale >15min"
        fi
    fi
}

# ── Check 2/3/4: RSI-Scalp +Trend fleet (v1.1, v2.0, v5.0) ─────────────
check_bot "v2.1" "$V21_DIR/bot.log" "$V21_DIR/state.json"
check_bot "v2.2" "$V22_DIR/bot.log" "$V22_DIR/state.json"

exit 0
