#!/bin/bash
# v1.1 = v1 + smart 6h time-SL (only fires on loss; winners ride to TP)
# Year-by-year custom backtest 2021-26: 6/6 years profitable, 1.21% max DD.
# Same as v1.1 prior + the new RSISCALP_SMART_TIME_SL=1 flag.
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_trend_v11.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_trend_v11/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=paper_rsiscalp_trend_v11 \
RSISCALP_TREND=1 \
RSISCALP_SMART_TIME_SL=1 \
  /usr/bin/flock -n /tmp/rsiscalpv11.lock \
  /usr/bin/python3 strategies/day/bot_rsiscalp_v11.py 2>&1 || exit 0
