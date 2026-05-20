#!/bin/bash
# self_heal.sh — Runs every 10 minutes via cron
# Checks: (1) dashboard server alive, (2) all three paper bots fired recently
# Restarts the dashboard server if down; logs an alert if any bot is stale

set -u
BOT_DIR="/home/jags/BTC-Flip-Bot"
LOG_FILE="$BOT_DIR/data/self_heal.log"
SR_DATA_DIR="$BOT_DIR/data/paper"           # V2.2 S/R paper bot
DIVFLIP_DATA_DIR="$BOT_DIR/data/paper_divflip"        # Divergence-Flip v1 paper bot
DIVFLIP_V2_DATA_DIR="$BOT_DIR/data/paper_divflip_v2"  # Divergence-Flip v2 paper bot (3rd)
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

# ── Check 2: V2.2 S/R paper bot ───────────────────────────────────
check_bot "V2.2 S/R" "$SR_DATA_DIR/bot_paper.log" "$SR_DATA_DIR/state_paper.json"

# ── Check 3: Divergence-Flip v1 paper bot ─────────────────────────
check_bot "Div-Flip" "$DIVFLIP_DATA_DIR/bot.log" "$DIVFLIP_DATA_DIR/state.json"

# ── Check 4: Divergence-Flip v2 paper bot (3rd bot) ───────────────
check_bot "Div-Flip v2" "$DIVFLIP_V2_DATA_DIR/bot.log" "$DIVFLIP_V2_DATA_DIR/state.json"

exit 0
