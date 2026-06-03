#!/bin/bash
# RSI-Scalp +Trend variant — same as rsiscalp but with a 15m EMA20/50 trend gate
# on entry (LONG only in 15m uptrend, SHORT only in downtrend). Runs in parallel
# with the plain rsiscalp bot for live A/B comparison. Own state in data/paper_rsiscalp_trend/.
#
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_trend.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_trend/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=paper_rsiscalp_trend RSISCALP_TREND=1 /usr/bin/python3 strategies/day/bot_rsiscalp.py 2>&1
