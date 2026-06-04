#!/bin/bash
# "gemini" paper bot — mainnet PUBLIC kline data + virtual $5K balance, 3x.
# Independent state/log in data/paper_gemini/. No API keys (public Binance only).
#
# Schedule via crontab (runs every minute):
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_gemini.sh >> /home/jags/BTC-Flip-Bot/data/paper_gemini/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
/usr/bin/python3 strategies/day/bot_gemini.py 2>&1
