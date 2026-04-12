#!/bin/bash
# Sets up both testnet + production cron jobs
# Each one is controlled by the toggle on the settings page

BOT_DIR="$HOME/Desktop/BTC-Flip-Bot"

# Remove any old bot cron entries
crontab -l 2>/dev/null | grep -v "bot.py" | grep -v "futures_bot" > /tmp/cron_clean

# Add both environments
cat >> /tmp/cron_clean << EOF
# BTC MACD+RSI Bot — Testnet (paper trading)
*/15 * * * * cd $BOT_DIR && /usr/bin/python3 bot.py --env testnet >> data/testnet/bot.log 2>&1

# BTC MACD+RSI Bot — Production (real money)
*/15 * * * * cd $BOT_DIR && /usr/bin/python3 bot.py --env production >> data/production/bot.log 2>&1
EOF

crontab /tmp/cron_clean
rm /tmp/cron_clean

echo "✓ Cron jobs installed:"
crontab -l | grep bot.py
echo ""
echo "Both environments will fire every 15 min."
echo "Use the settings page to enable/disable each one independently."
