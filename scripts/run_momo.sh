#!/bin/bash
# MOMO v1 — daily trend-momentum paper bot (long/flat spot, honest accounting).
#   LONG when daily close > SMA200 AND RSI14 > 70 within last 7 closed days.
#   Validated 2026-06-12: +139.6% / 5.1y, maxDD 21%, 24 trades, in-market 13%.
#   Fees+slippage charged on every paper fill; exits booked at LIVE price.
#
# Schedule via crontab (runs every 15 min; trades only when a new day closes):
#   */15 * * * * /home/jags/BTC-Flip-Bot/scripts/run_momo.sh >> /home/jags/BTC-Flip-Bot/data/momo_v1/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
MOMO_DATA_DIR=momo_v1 \
  /usr/bin/flock -n /tmp/momo_v1.lock \
  /usr/bin/python3 bot/bot_momo_daily.py 2>&1 || exit 0
