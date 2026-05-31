"""core_divflip_v2.py — v2: slower trend filter (Option B from 2026-05-31 analysis).

Spec change vs v1:
  - Trend timeframe: 15m -> 1h
  - Trend EMAs: 20/50 -> 50/200

Rationale: v1 had a structural tension where RSI extremes (≤40 bull / ≥70 bear)
typically form during opposite-direction trends, so the 15m EMA20/50 filter
blocked most divergence entries. The user observed this directly in logs:
fresh divergences forming but Signal=NONE because the 15m trend hadn't
flipped yet.

v2 keeps the trend filter (bear-regime protection: -80% -> -14%) but moves
it to a slower timeframe where the filter only blocks during genuine sustained
trends, not during ranging chop where the 15m filter whipsaws.

Everything else stays identical to v1:
  - RSI ≤40 LONG / ≥70 SHORT
  - 5m signal, pivot 5L/1R, RSI(10)
  - 2 DCA legs @ 0.35%, SL 0.7%, TP 0.5%, BE 0.3%, profit trail
  - Friday block, IST night block, same-level block, one-shot per pivot
  - Cooldowns: 30min loss, 15min TP

Runs in parallel with v1 via separate cron / data dir (paper_divflip_v2/).
After ~2 weeks of paper data, compare trade rate + WR + PnL to decide
whether v2 supersedes v1.
"""
from __future__ import annotations

# Inherit everything from v1 core
from core_divflip import *  # noqa: F401, F403

# ═════ v2 OVERRIDES ═════
# Option B: slower trend filter.
TREND_TIMEFRAME    = "1h"     # was "15m"
TREND_EMA_FAST     = 50       # was 20
TREND_EMA_SLOW     = 200      # was 50
