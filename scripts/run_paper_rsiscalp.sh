#!/bin/bash
# Pure-RSI mean-reversion scalper — paper-trading bot.
# Mainnet PUBLIC kline data + virtual fills + virtual $5K balance.
# Independent from divflip — own state/log in data/paper_rsiscalp/.
# No API keys needed (only public Binance endpoints).
#
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
/usr/bin/python3 strategies/day/bot_rsiscalp.py 2>&1
