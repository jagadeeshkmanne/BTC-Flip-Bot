#!/bin/bash
# v1.1 = v1 + 72-bar time-based SL
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_trend_v11.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_trend_v11/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=paper_rsiscalp_trend_v11 RSISCALP_TREND=1 \
  /usr/bin/flock -n /tmp/rsiscalpv11.lock \
  /usr/bin/python3 strategies/day/bot_rsiscalp_v11.py 2>&1 || exit 0
