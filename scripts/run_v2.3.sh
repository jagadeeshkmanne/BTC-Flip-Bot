#!/bin/bash
# v2.3 REGIME ROUTER (2026-06-14): one bot, two legs switched by 1h ADX.
#   1h ADX >= 25 -> TREND leg  (with-trend, RSI 30/70, gap 0.15%, TP 0.5/0.25%)
#   1h ADX <  20 -> RANGE leg  (counter-trend, RSI 35/65, gap 0.20%, TP 0.5/1.0%)
#   20-25        -> FLAT (no new entries; open positions still managed)
# Honest backtest (FINDINGS #14): NOT profitable; best is trend-only at -20%/90d.
# Deployed to OBSERVE the regime switch live (paper only).
# To run the least-bad trend-only config: set RSISCALP_REGIME_RANGE_ON=0 below.
# NOTE: do NOT put comments between the backslash-continued env lines below.
cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=v2.3 \
RSISCALP_LEVERAGE=5.0 \
RSISCALP_WEEKEND_QTY_MULT=1.0 \
RSISCALP_TREND=1 \
RSISCALP_SMART_TIME_SL=0 \
RSISCALP_BE_WAIT_BARS=6 \
RSISCALP_TIME_SL_BARS=72 \
RSISCALP_MTM_STOP_PCT=0 \
RSISCALP_DAILY_MAX_LOSS_PCT=0 \
RSISCALP_ATR_MAX_PCT=0.80 \
RSISCALP_REGIME_TF=15m \
RSISCALP_REGIME_ADX_LEN=14 \
RSISCALP_REGIME_TREND_ADX=25 \
RSISCALP_REGIME_RANGE_ADX=20 \
RSISCALP_REGIME_RANGE_ON=1 \
  /usr/bin/flock -n /tmp/v2.3.lock \
  /usr/bin/python3 bot/bot_rsiscalp_v3.py 2>&1 || exit 0
