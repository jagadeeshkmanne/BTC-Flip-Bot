#!/bin/bash
# RSI-Scalp +Trend v5 — v1 entries + NO DCA + tight SL (0.5% from entry).
# Same simple entries as v1 (no GAP filter, no anti-breakout) BUT with v4-style
# risk management. Tests "simple entries + bounded loss" hypothesis.
#
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_trend_v5.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_trend_v5/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=paper_rsiscalp_trend_v5 \
RSISCALP_TREND=1 \
  /usr/bin/flock -n /tmp/rsiscalp_v5.lock /usr/bin/python3 strategies/day/bot_rsiscalp_v5.py 2>&1 || exit 0
