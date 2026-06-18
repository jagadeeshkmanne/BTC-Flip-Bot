#!/bin/bash
# Dynamic-Leverage Trend (4h BTC perp, long/flat) paper bot. No env block (avoids the v2.2 inline-comment bug).
cd /home/jags/BTC-Flip-Bot
/usr/bin/flock -n /tmp/trend.lock /usr/bin/python3 bot/bot_trend_4h.py 2>&1 || exit 0
