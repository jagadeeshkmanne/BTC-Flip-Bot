#!/bin/bash
# ─── BTC Bot Self-Healer ───
# Runs every 5 minutes via cron. Checks all services and auto-restarts if unhealthy.
# Install: crontab -e → */5 * * * * /home/$USER/BTC-Flip-Bot/scripts/self_heal.sh >> /home/$USER/BTC-Flip-Bot/data/heal.log 2>&1

LOG="/home/$USER/BTC-Flip-Bot/data/heal.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
HEALED=0

# ─── 1. Check Dashboard Server (should always be running and responding) ───
SERVER_ACTIVE=$(systemctl is-active btc-bot-server 2>/dev/null)

if [ "$SERVER_ACTIVE" != "active" ]; then
    echo "$TIMESTAMP [HEAL] Dashboard server is $SERVER_ACTIVE — restarting..."
    sudo systemctl restart btc-bot-server
    sleep 3
    HEALED=1
fi

# Even if systemd says active, check if it actually responds
if [ "$SERVER_ACTIVE" = "active" ]; then
    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 http://localhost:8888/dashboard.html 2>/dev/null)
    if [ "$HTTP_CODE" != "200" ]; then
        echo "$TIMESTAMP [HEAL] Dashboard server frozen (HTTP $HTTP_CODE) — restarting..."
        sudo systemctl restart btc-bot-server
        sleep 3
        HEALED=1
    fi
fi

# ─── 2. Check Testnet Timer (should be active and waiting) ───
TESTNET_TIMER=$(systemctl is-active btc-bot-testnet.timer 2>/dev/null)

if [ "$TESTNET_TIMER" != "active" ]; then
    echo "$TIMESTAMP [HEAL] Testnet timer is $TESTNET_TIMER — restarting..."
    sudo systemctl restart btc-bot-testnet.timer
    HEALED=1
fi

# ─── 3. Check Production Timer (should be active and waiting) ───
PROD_TIMER=$(systemctl is-active btc-bot-production.timer 2>/dev/null)

if [ "$PROD_TIMER" != "active" ]; then
    echo "$TIMESTAMP [HEAL] Production timer is $PROD_TIMER — restarting..."
    sudo systemctl restart btc-bot-production.timer
    HEALED=1
fi

# ─── 4. Check if bot ran recently (within last 20 min for 15m interval) ───
TESTNET_LOG="/home/$USER/BTC-Flip-Bot/data/testnet/bot.log"
if [ -f "$TESTNET_LOG" ]; then
    LAST_MOD=$(stat -c %Y "$TESTNET_LOG" 2>/dev/null || stat -f %m "$TESTNET_LOG" 2>/dev/null)
    NOW=$(date +%s)
    AGE=$(( NOW - LAST_MOD ))
    # If log hasn't been updated in 25 minutes, timer might be stuck
    if [ "$AGE" -gt 1500 ]; then
        echo "$TIMESTAMP [HEAL] Testnet bot hasn't run in ${AGE}s — restarting timer..."
        sudo systemctl restart btc-bot-testnet.timer
        HEALED=1
    fi
fi

# ─── 5. Check memory (e2-micro can run out) ───
MEM_AVAIL=$(awk '/MemAvailable/ {print $2}' /proc/meminfo 2>/dev/null)
if [ -n "$MEM_AVAIL" ] && [ "$MEM_AVAIL" -lt 51200 ]; then
    # Less than 50MB available — critical
    echo "$TIMESTAMP [HEAL] Low memory (${MEM_AVAIL}kB free) — clearing caches..."
    sync && echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null
    HEALED=1
fi

# ─── 6. Check swap is active (required for e2-micro) ───
SWAP_TOTAL=$(awk '/SwapTotal/ {print $2}' /proc/meminfo 2>/dev/null)
if [ -n "$SWAP_TOTAL" ] && [ "$SWAP_TOTAL" -lt 1024 ]; then
    echo "$TIMESTAMP [HEAL] No swap detected — enabling..."
    if [ -f /swapfile ]; then
        sudo swapon /swapfile 2>/dev/null
    else
        sudo fallocate -l 512M /swapfile
        sudo chmod 600 /swapfile
        sudo mkswap /swapfile
        sudo swapon /swapfile
        echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
    fi
    HEALED=1
fi

# ─── Summary ───
if [ "$HEALED" -eq 1 ]; then
    echo "$TIMESTAMP [HEAL] Self-healing actions completed."
else
    # Only log every hour to keep log file small (minute 0 or 5)
    MINUTE=$(date '+%M')
    if [ "$MINUTE" = "00" ] || [ "$MINUTE" = "05" ]; then
        echo "$TIMESTAMP [OK] All services healthy."
    fi
fi
