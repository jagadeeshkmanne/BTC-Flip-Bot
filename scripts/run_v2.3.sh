#!/bin/bash
# v2.3 RSI TREND ALIGNED (2026-06-12):
#   - Shift to 1-Hour Timeframe
#   - 1.0% Take Profit minimum (Daily TP goal)
#   - DCA: 1 leg at 1.0% adverse spacing
#   - Strict Trend Filter: 1H EMA 50 > 200 required for LONG
#   - No counter-trend falling knife catching!

cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=v2.3 \
RSISCALP_LEVERAGE=5.0 \
RSISCALP_RSI_PERIOD=14 \
RSISCALP_RSI_OVERSOLD=35 \
RSISCALP_RSI_OVERBOUGHT=65 \
RSISCALP_TP_SINGLE=0.010 \
RSISCALP_TP_DCA=0.010 \
RSISCALP_DCA_SPACING=0.010 \
RSISCALP_SL_FROM_WORST=0.015 \
RSISCALP_BE_AFTER_DCA=1 \
RSISCALP_BE_WAIT_BARS=0 \
RSISCALP_TREND=1 \
RSISCALP_COUNTER_TREND=0 \
RSISCALP_MTM_STOP_PCT=0.05 \
  /usr/bin/flock -n /tmp/v2.3.lock \
  /usr/bin/python3 bot/bot_v2_3.py 2>&1 || exit 0
