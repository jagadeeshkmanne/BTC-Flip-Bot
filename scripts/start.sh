#!/bin/bash
# Start the MACD+RSI trading bot
# Usage: ./scripts/start.sh [testnet|production]
cd "$(dirname "$0")/.."
ENV=${1:-testnet}

if pgrep -f "bot.py --env $ENV" > /dev/null; then
    echo "[$ENV] Bot is already running."
    echo "PID(s): $(pgrep -f "bot.py --env $ENV")"
    echo "To stop: ./scripts/stop.sh $ENV"
    exit 0
fi

nohup /usr/bin/python3 bot.py --env $ENV >> data/$ENV/bot.log 2>&1 &
BOT_PID=$!
sleep 2

if ps -p $BOT_PID > /dev/null; then
    echo "[$ENV] Bot started. PID: $BOT_PID"
    echo "Live log:  tail -f data/$ENV/bot.log"
    echo "Stop bot:  ./scripts/stop.sh $ENV"
else
    echo "[$ENV] Bot failed to start. Check data/$ENV/bot.log:"
    tail -20 data/$ENV/bot.log
    exit 1
fi
