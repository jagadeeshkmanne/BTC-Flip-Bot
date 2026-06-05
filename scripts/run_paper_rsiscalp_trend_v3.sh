#!/bin/bash
# RSI-Scalp +Trend v3 — RISK-BASED architecture (Gemini-style).
# Single entry (no DCA) + 0.5% balance risk capped per trade + GAP filter from v2.
#
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_trend_v3.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_trend_v3/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=paper_rsiscalp_trend_v3 \
RSISCALP_TREND=1 \
RSISCALP_V2_GAP_MIN=0.0025 \
  /usr/bin/python3 strategies/day/bot_rsiscalp_v3.py 2>&1
