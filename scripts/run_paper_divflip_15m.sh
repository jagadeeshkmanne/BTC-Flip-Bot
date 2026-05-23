#!/bin/bash
# Paper Bot 5 — divflip on 15-minute timeframe, fully tuned for higher TF.
#   - 7L/1R pivot, RSI period 7, L≤45/S≥65, 21-bar freshness
#   - 2 DCAs at 0.75% spacing, weights 3:4
#   - SL 2% L1-anchored, BE +2% from avg, trail 0.2%
#   - NO fixed TP, NO cooldown, NO max_hold
#   - 45-day backtest: +$3,990 / +79.8% / PF 3.98 / DD 4.7%
cd /home/jags/BTC-Flip-Bot
/usr/bin/python3 strategies/day/bot_divflip_15m.py 2>&1
