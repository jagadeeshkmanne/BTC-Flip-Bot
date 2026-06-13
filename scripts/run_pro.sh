#!/bin/bash
# PRO SWING STRATEGY:
#   - 4H Timeframe
#   - EMA 50/200 Crossover Bidirectional
#   - 4.0 ATR Trailing Stop
#   - Designed for high-conviction 5x leverage trends

cd /home/jags/BTC-Flip-Bot

PRO_DATA_DIR=pro_4h \
PRO_LEVERAGE=5.0 \
PRO_TIMEFRAME=4h \
PRO_EMA_FAST=50 \
PRO_EMA_SLOW=200 \
PRO_TRAIL_ATR=4.0 \
PRO_ATR_PERIOD=14 \
  /usr/bin/flock -n /tmp/pro_swing.lock \
  /usr/bin/python3 bot/bot_pro.py 2>&1 || exit 0
