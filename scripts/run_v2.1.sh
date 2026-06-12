#!/bin/bash
# 2026-06-12: time-SL switched SMART(6h losers-only) -> HARD 6h (user req):
#   close the basket unconditionally after 72 bars, booked at LIVE price.
# 2026-06-12: MTM basket stop REMOVED (user req) — RSISCALP_MTM_STOP_PCT=0.
#   Risk note: a DCAd basket in the 6-bar BE-wait window now has NO price stop;
#   only the HARD 6h time stop bounds it (honest sim worst trade -$818 vs -$270 with MTM).
#   Honest sweep evidence (timestop_sweep REAL mode): hard vs smart ~= $0
#   difference (positions usually resolve <3h); this is risk hygiene, not edge.
# v2.1 COUNTER-TREND (2026-06-10):
#   v2 (counter-trend RSI 35/65 + GAP 0.20% + BE wait 6 + ATR 0.8%)
#   + 5× leverage (up from 3×) → L1+L2 each = $11,875 (was $7,125)
#   + Weekend boost DISABLED (was 2×) → consistent sizing every day
#   + Profit-only trend-flip exit + L2 trail SL (deployed 2026-06-10)
#   + Zero fees in paper (deployed 2026-06-10)
#
# 5y backtest (margin enforced, fee-free):
#   13,501 trades / 70.7% WR / +$481,785 / 1.26% DD
#   vs current 3× lev with weekend 2× (margin enforced): $324K / 0.76% DD
#   v2.1 trade-off: +0.50% more DD for +$157K more profit (no rejections)
#
# At 5× lev, liquidation distance ~20% (vs ~33% at 3×). Still well past the
# 0.6% SL — no realistic liquidation risk for our scalping strategy.
#
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_trend_v3.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_trend_v3/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=v2.1 \
RSISCALP_LEVERAGE=5.0 \
RSISCALP_WEEKEND_QTY_MULT=1.0 \
RSISCALP_TREND=1 \
RSISCALP_SMART_TIME_SL=0 \
RSISCALP_TIME_SL_BARS=72 \
RSISCALP_RSI_OVERSOLD=30 \
RSISCALP_RSI_OVERBOUGHT=70 \
RSISCALP_V2_GAP_MIN=0.0015 \
RSISCALP_ATR_MAX_PCT=0.80 \
RSISCALP_BE_WAIT_BARS=6 \
RSISCALP_COUNTER_TREND=0 \
RSISCALP_MTM_STOP_PCT=0 \
RSISCALP_DAILY_MAX_LOSS_PCT=0 \
  /usr/bin/flock -n /tmp/v2.1.lock \
  /usr/bin/python3 bot/bot_rsiscalp_v3.py 2>&1 || exit 0
