#!/bin/bash
# Paper Bot 4 — divflip v1f (v1 + improvements):
#   - DCA_LEVELS 3 → 2 (removed L3 — bounded worst-case loss)
#   - SL anchored to first_entry (not worst_entry — catches losses earlier)
#   - 24h cooldown after any SL exit (avoids re-entry into ongoing downtrend)
# Independent of v1 paper bot — own state/log in data/paper_divflip_v1f/.
cd /home/jags/BTC-Flip-Bot
/usr/bin/python3 strategies/day/bot_divflip_v1f.py 2>&1
