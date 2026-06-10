#!/bin/bash
# v2 ORIGINAL COUNTER-TREND (restored 2026-06-10):
#   Same counter-trend strategy as v2.1 but ORIGINAL sizing kept for
#   side-by-side A/B comparison with the new v2.1 (5× / no weekend).
#
#   Leverage:       3× (default, unchanged)
#   L1/L2 notional: $7,125 each (auto = balance × 0.95 × 3 / 2)
#   Weekend:        2× boost
#   Other:          RSI 35/65, GAP 0.20%, BE wait 6, ATR 0.80%
#
# 13-month linear backtest (margin enforced, fee-free):
#   v2:    1,844 trades / 66.3% WR / +$36,264 / 13/13 months profitable
#   v2.1:  2,139 trades / 65.7% WR / +$69,064 / 13/13 months profitable
#
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_trend_v2.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_trend_v2/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=paper_rsiscalp_trend_v2 \
RSISCALP_LEVERAGE=3.0 \
RSISCALP_WEEKEND_QTY_MULT=2.0 \
RSISCALP_TREND=1 \
RSISCALP_SMART_TIME_SL=1 \
RSISCALP_RSI_OVERSOLD=35 \
RSISCALP_RSI_OVERBOUGHT=65 \
RSISCALP_V2_GAP_MIN=0.0020 \
RSISCALP_ATR_MAX_PCT=0.80 \
RSISCALP_BE_WAIT_BARS=6 \
RSISCALP_COUNTER_TREND=1 \
RSISCALP_TP_DCA=0.01 \
RSISCALP_TIME_SL_BARS=144 \
  /usr/bin/flock -n /tmp/rsiscalpv2.lock \
  /usr/bin/python3 strategies/day/bot_rsiscalp_v3.py 2>&1 || exit 0
