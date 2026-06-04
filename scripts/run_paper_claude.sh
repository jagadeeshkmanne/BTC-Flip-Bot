#!/bin/bash
# "claude" paper bot — mainnet PUBLIC kline data + virtual $5K balance, 3x.
# Independent state/log in data/paper_claude/. No API keys (public Binance only).
#
# Schedule via crontab (runs every minute):
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_claude.sh >> /home/jags/BTC-Flip-Bot/data/paper_claude/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
/usr/bin/python3 strategies/day/bot_claude.py 2>&1
