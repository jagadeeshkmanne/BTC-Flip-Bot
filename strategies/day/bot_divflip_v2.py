#!/usr/bin/env python3
"""bot_divflip_v2.py — v2 paper bot runner. Same logic as v1, slower trend filter.

This is a THIN WRAPPER around bot_divflip.py:
  1. Sets DIVFLIP_DATA_DIR=paper_divflip_v2 so the v1 bot writes to a
     separate state/log/status directory.
  2. Substitutes sys.modules['core_divflip'] with core_divflip_v2 so the
     v1 bot's `from core_divflip import ...` resolves to the v2 overrides
     (1h trend filter, 50/200 EMAs).

Result: v1 logic runs unchanged, with only the trend timeframe/EMAs swapped
and a separate data directory. No code duplication; future v1 fixes flow
automatically to v2.

Run via:
  python3 strategies/day/bot_divflip_v2.py
or:
  bash scripts/run_paper_divflip_v2.sh

Schedule via cron (every 1 min, see run_paper_divflip_v2.sh).
"""
from __future__ import annotations
import os, sys

# 1. Tell v1 bot to write to v2's own data directory
os.environ["DIVFLIP_DATA_DIR"] = "paper_divflip_v2"

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, STRATEGY_DIR)

# 2. Alias core_divflip -> core_divflip_v2 BEFORE importing bot_divflip
#    so its top-level `from core_divflip import ...` picks up v2 constants.
import core_divflip_v2 as _v2_core
sys.modules["core_divflip"] = _v2_core

# 3. Import bot_divflip (module-level code runs: imports, logger setup) and
#    call its main() explicitly. When run via `python3 bot_divflip.py`,
#    `__name__ == "__main__"` triggers main(); when imported here, it doesn't,
#    so we must invoke it ourselves.
import bot_divflip  # noqa: E402

if __name__ == "__main__":
    bot_divflip.main()
