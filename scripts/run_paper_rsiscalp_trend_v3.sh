#!/bin/bash
# v3 COUNTER-TREND OPTIMAL (2026-06-08):
#   RSI 35/65 + GAP 0.20% + BE wait 6 + ATR 0.8% + COUNTER-TREND
# 5y backtest (honest, no lookahead):
#   12,859 trades / 65.0% WR / +$173,910 / 1.29% DD / PF 3.07
#   vs current v1.1: +303% trades, +14pp WR, +510% profit, LOWER DD
#
# KEY: counter-trend bypasses the 15m trend gate so RSI extremes fire
# regardless of trend. BE-after-DCA + 6-bar wait + smart 6h time-SL
# keep risk tight despite higher trade count.
#
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_trend_v3.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_trend_v3/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=paper_rsiscalp_trend_v3 \
RSISCALP_TREND=1 \
RSISCALP_SMART_TIME_SL=1 \
RSISCALP_RSI_OVERSOLD=35 \
RSISCALP_RSI_OVERBOUGHT=65 \
RSISCALP_V2_GAP_MIN=0.0020 \
RSISCALP_ATR_MAX_PCT=0.80 \
RSISCALP_BE_WAIT_BARS=6 \
RSISCALP_COUNTER_TREND=1 \
  /usr/bin/flock -n /tmp/rsiscalpv3.lock \
  /usr/bin/python3 strategies/day/bot_rsiscalp_v3.py 2>&1 || exit 0
