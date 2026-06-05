#!/bin/bash
# RSI-Scalp +Trend v4 — v3 entries + NO DCA + tight SL (0.5% from entry).
# Same entry filters as v3 (GAP + anti-breakout). Risk-capped at ~$75/trade.
#
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_trend_v4.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_trend_v4/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=paper_rsiscalp_trend_v4 \
RSISCALP_TREND=1 \
RSISCALP_V2_GAP_MIN=0.0025 \
  /usr/bin/python3 strategies/day/bot_rsiscalp_v4.py 2>&1
