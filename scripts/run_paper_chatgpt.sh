#!/bin/bash
# "chatgpt" paper bot — mainnet PUBLIC kline data + virtual $5K balance, 3x.
# Independent state/log in data/paper_chatgpt/. No API keys (public Binance only).
#
# Schedule via crontab (runs every minute):
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_chatgpt.sh >> /home/jags/BTC-Flip-Bot/data/paper_chatgpt/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
/usr/bin/python3 strategies/day/bot_chatgpt.py 2>&1
