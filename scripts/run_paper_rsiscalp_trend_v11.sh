#!/bin/bash
# v1.1 OPTIMAL (2026-06-08): RSI 35/65 + GAP 0.15% + BE wait 3 + ATR 0.8%
# 5y backtest (honest, pessimistic, no lookahead):
#   6,782 trades / 55.2% WR / +$64,174 / 2.08% DD / PF 2.13
#   vs old config: +113% trades, +125% profit, same DD
# Uses v3 bot code with COUNTER_TREND=0 (with-trend mode).
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_trend_v11.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_trend_v11/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=paper_rsiscalp_trend_v11 \
RSISCALP_TREND=1 \
RSISCALP_SMART_TIME_SL=1 \
RSISCALP_RSI_OVERSOLD=35 \
RSISCALP_RSI_OVERBOUGHT=65 \
RSISCALP_V2_GAP_MIN=0.0015 \
RSISCALP_ATR_MAX_PCT=0.80 \
RSISCALP_BE_WAIT_BARS=3 \
RSISCALP_COUNTER_TREND=0 \
RSISCALP_TP_DCA=0.01 \
RSISCALP_TIME_SL_BARS=144 \
  /usr/bin/flock -n /tmp/rsiscalpv11.lock \
  /usr/bin/python3 strategies/day/bot_rsiscalp_v3.py 2>&1 || exit 0
