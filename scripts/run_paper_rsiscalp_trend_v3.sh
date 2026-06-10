#!/bin/bash
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
RSISCALP_DATA_DIR=paper_rsiscalp_trend_v3 \
RSISCALP_LEVERAGE=5.0 \
RSISCALP_WEEKEND_QTY_MULT=1.0 \
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
