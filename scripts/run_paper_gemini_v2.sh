#!/bin/bash
# Gemini v2 paper bot — trend-only scalper, true 3× notional, ATR spike lock,
# 24h equity halt, EMA20 structural trail. Mainnet PUBLIC kline data only.
# Independent state/log in data/paper_gemini_v2/. No API keys (public Binance).
#
# Schedule via crontab (runs every minute):
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_gemini_v2.sh >> /home/jags/BTC-Flip-Bot/data/paper_gemini_v2/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
/usr/bin/python3 strategies/day/bot_gemini_v2.py 2>&1
