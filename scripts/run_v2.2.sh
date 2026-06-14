#!/bin/bash
# 2026-06-12: time-SL switched SMART 12h (losers-only) -> HARD 6h (user req):
#   close the basket unconditionally after 72 bars, booked at LIVE price.
#   Honest sweep evidence (timestop_sweep REAL mode): all time-stop variants
#   within ~$50 of each other at PF ~0.64 — risk hygiene, not edge.
# v2.2 COUNTER-TREND OPTIMIZED L2 EXITS (2026-06-10):
#   v2.1 base config (5× lev, no weekend, counter-trend) + 2 changes:
#     TP for L2 (DCA): 0.25% → 1.00%   (catch bigger bounces)
#     Smart time-SL:   6h → 12h         (give positions more recovery time)
#
# 6.8y backtest (validated, fee-free linear):
#   v2.1:  19,889 trades / 71.9% WR / +$726,884 / 1.27% DD
#   v2.2:  19,140 trades / 72.1% WR / +$884,471 / 0.64% DD
#          +$157K profit (+22%), DD HALVED (-49%)
#
# Per-year: v2.2 wins every year on profit, no year worse on DD.
#
# Runs side-by-side with v2.1 for live A/B comparison.
#
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_trend_v22.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_trend_v22/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=v2.2 \
RSISCALP_LEVERAGE=5.0 \
RSISCALP_WEEKEND_QTY_MULT=1.0 \
RSISCALP_TREND=1 \
RSISCALP_SMART_TIME_SL=0 \
RSISCALP_RSI_OVERSOLD=30 \
RSISCALP_RSI_OVERBOUGHT=70 \
RSISCALP_V2_GAP_MIN=0.0020 \
RSISCALP_ATR_MAX_PCT=0.80 \
RSISCALP_BE_WAIT_BARS=6 \
RSISCALP_COUNTER_TREND=1 \
RSISCALP_TP_DCA=0.0025 \
RSISCALP_TIME_SL_BARS=72 \
RSISCALP_MTM_STOP_PCT=0 \
RSISCALP_DAILY_MAX_LOSS_PCT=0 \
  /usr/bin/flock -n /tmp/v2.2.lock \
  /usr/bin/python3 bot/bot_rsiscalp_v3.py 2>&1 || exit 0
