#!/bin/bash
# Check trading bot status
# Usage: ./scripts/status.sh [testnet|production]
cd "$(dirname "$0")/.."
ENV=${1:-testnet}

echo "═══════════════════════════════════════════"
echo "   BTC MACD+RSI BOT [$ENV]"
echo "═══════════════════════════════════════════"

PIDS=$(pgrep -f "bot.py --env $ENV")

if [ -z "$PIDS" ]; then
    echo "Status:  STOPPED"
    echo "To start: ./scripts/start.sh $ENV"
else
    echo "Status:  RUNNING"
    echo "PID(s):  $PIDS"
    for PID in $PIDS; do
        START=$(ps -p $PID -o lstart= 2>/dev/null)
        echo "Started: $START"
    done
fi

echo ""
echo "─── Last 15 log lines ───"
[ -f "data/$ENV/bot.log" ] && tail -15 "data/$ENV/bot.log" || echo "(No log file yet)"

echo ""
echo "─── State ───"
if [ -f "data/$ENV/state.json" ]; then
    /usr/bin/python3 -c "
import json
with open('data/$ENV/state.json') as f:
    s = json.load(f)
print(f\"Last run:        {s.get('last_run', 'never')}\")
print(f\"Active positions: {len(s.get('positions', {}))}\")
print(f\"Total trades:    {s.get('stats', {}).get('total_trades', 0)}\")
print(f\"Wins:            {s.get('stats', {}).get('wins', 0)}\")
print(f\"Losses:          {s.get('stats', {}).get('losses', 0)}\")
print(f\"Total P&L:       \${s.get('stats', {}).get('total_profit_usd', 0):.2f}\")
" 2>/dev/null || echo "(Could not parse state)"
else
    echo "(No state file yet)"
fi
