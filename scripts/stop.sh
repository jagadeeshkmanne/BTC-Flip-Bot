#!/bin/bash
# Stop the trading bot
# Usage: ./scripts/stop.sh [testnet|production]
ENV=${1:-testnet}
PIDS=$(pgrep -f "bot.py --env $ENV")

if [ -z "$PIDS" ]; then
    echo "[$ENV] Bot is not running."
    exit 0
fi

echo "[$ENV] Stopping bot (PIDs: $PIDS)..."
pkill -f "bot.py --env $ENV"
sleep 1

if pgrep -f "bot.py --env $ENV" > /dev/null; then
    pkill -9 -f "bot.py --env $ENV"
    sleep 1
fi

echo "[$ENV] Bot stopped."
