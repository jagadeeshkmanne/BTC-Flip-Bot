#!/bin/bash
# Gemini v3 paper bot — dual-engine (Trend Pullback + Liquidity Sweep), true 3×,
# dynamic prev_day levels, ATR spike lock, 24h equity halt with auto-resume.
# Mainnet PUBLIC kline data only. Independent state/log in data/paper_gemini_v3/.
# No API keys (public Binance only).
#
# Schedule via crontab (runs every minute):
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_gemini_v3.sh >> /home/jags/BTC-Flip-Bot/data/paper_gemini_v3/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
/usr/bin/python3 strategies/day/bot_gemini_v3.py 2>&1
