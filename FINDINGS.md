# BTC-Flip-Bot — Established Findings (read before analyzing anything)

Updated 2026-06-11. Every claim below is backed by an honest backtest
(no lookahead, honest stop fills, real fees). Do NOT trust any dashboard
history or backtest result predating 2026-06-11 — see finding #1.

## 1. The stop-fill booking artifact (CRITICAL context)

Until 2026-06-11 the paper bots and all backtests booked stop-family exits
(SL / BE-DCA / L2_TRAIL) AT the stop price even when the market was already
beyond it. BE-DCA exits were recorded as $0 "neutrals" — they are real
losses. This fiction was the ENTIRE apparent edge of the v2.x bots
(fictional: +$884K/5y; honest: PF ~1.0 at zero fees, -100% with fees).
Fixed in bot_rsiscalp_v3.py 2026-06-11 (exits book live price, real fees
charged, histories archived and reset). Any old number you see — dashboards,
memory of "+6.63%", old backtests — predates the fix and is invalid.

## 2. The 5m entries carry zero information — measured seven ways

The live entries (RSI9 35/65 counter-trend + 15m EMA gap + ATR gates) were
tested under SEVEN exit families: BE+trail (live design), plain stop, resting
limit at avg, fixed TP/SL (5 geometries), signal exits, martingale, and S/R-
based exits. Win rates ranged 8%-78%; zero-fee PF stayed 0.92-1.01 in ALL of
them, matching the random-walk barrier formula WR = SL/(TP+SL) within ~1pt.
Conclusion: exits cannot fix these entries. No further 5m exit/R:R/wrapper
ideas are worth testing.

R:R FRONTIER (2026-06-12, backtest/rr_frontier.py): 48-cell TP x SL grid
(0.25-3% x 0.25-2%) on the live entries, single position, honest fills,
2021-2026. COUNTER-trend (live direction): all 24 cells gross-NEGATIVE even
at zero cost; WR tracks the random-walk barrier WR*=SL/(TP+SL) within ~1-3pt
everywhere. WITH-trend (flipped): best cell TP3%/SL2% gross +0.036%/trade —
4x SMALLER than taker round-trip (~0.15%) and ~equal to maker (~0.04%); the
only gross-positive cells are the widest barriers (multi-hour holds = the
daily-timeframe gradient of #4 in disguise). THE BEST R:R EXISTS (~1.5
with-trend) AND IS STILL UNPROFITABLE. R:R reshapes the win-rate/win-size
trade-off; it cannot create expectancy. Don't re-sweep.

TIME-STOP SWEEP (2026-06-12, backtest/timestop_sweep.py): the "losers linger,
winners are quick" premise is FALSE for these entries — honest hold-time
profile: winners median 0.75-0.83h vs losers median 1.0h (nearly identical);
by 2h, 82-87% of win-$ is earned but only 12-17% of loss-$ comes from trades
held longer. Swept smart (close-if-losing) and HARD (unconditional) time
stops at 1h/2h/4h/12h on the live config + MTM stop: every stop tighter than
the deployed 6h/12h smart time-SL made results WORSE (v2.2 paper +$33K ->
+$0.5-25K; 1h stops are the worst). REAL mode: negligible effect, PF stays
0.52-0.65. The deployed smart time-SL is already at the optimum. Closed.

EXTERNAL-REVIEW PROPOSALS (2026-06-12, backtest/proposal_stack.py): a code
review proposed (1) confirmation-stack entries (15m trend + RSI14 30/70 + BB
(20,2) touch + confirming candle + volume>SMA20), (2) ADX>25 EMA20-pullback
entries, (3) ADX regime switcher (trend->pullback, range->mean-reversion,
"single biggest improvement in crypto bots"), all with TP 0.8-1.2% / SL
0.5-0.7%, no DCA. Honest test, 2019-2026, IS/OOS: ALL 18 family x TP x SL
cells GROSS-NEGATIVE full-sample (best OOS subset +0.04%/trade = noise after
IS -0.09%, still 4x below taker costs). The stack cut signal count 70x
("eliminates many bad trades") — and the good ones at the same rate. The
review's DIAGNOSIS (entries are the bottleneck, exits can't fix them, drop
martingale) matches #2; its PRESCRIPTIONS fail like every other 5m entry
family. Confirmation/filter stacking does not create 5m information.

DAILY-REGIME PAUSE GATES (2026-06-12): the last untested "when to pause"
variant — gate scalper entries on YESTERDAY'S completed daily regime (the
one timescale with real information): daily ADX<20/<25, |dist SMA200|<5%,
daily ATR<100d median. Result: ADX<20 "range" selects a WORSE subset (0-fee
PF 0.918 vs baseline 1.041 — "we profit in range" is FALSE at daily
definition); best gate (near-SMA200) adds ~+1.3bp/trade gross (PF 1.080),
~15x below costs; all real-fee cells bleed the same −$24-29/trade. Pause
detection is now closed at EVERY timescale: intraday (ADX/range-pos/vol/
hours), day-clustering (none), daily regime (this). Daily information is
real but does not transfer through the scalper's per-trade cost floor.

ML ENTRY-SKIP CEILING (2026-06-12, backtest/predict_entry_skip.py): gradient-
boosted classifier on 13 signal-time features (trend, depth, ATR, rp24, hour,
dow, 1h/6h momentum, 24h vol, prev-trade outcome), trained on 5,396 honest
trades ..2022, skip-threshold fixed on train, evaluated on 4,412 trades 2023+:
train AUC 0.764 vs TEST AUC 0.525 — textbook overfit demonstration + the
measured ceiling of ALL indicators combined. Train-fixed skip rule keeps 27%
of OOS trades: avg −$25.53 → −$22.01/trade (the known sub-cost whispers:
vol24h, rp24, hour); total improves only via 73% fewer trades (pacing).
No feature combination flips the sign; per-trade loss-avoidance on these
entries is closed at the multivariate level.

GAP & RSI-DEPTH SWEEPS — the global entry optimum located (2026-06-12):
gap firmness is monotonic & passes the looser-control test (0.10% PF 1.025 →
0.60% PF 1.143, both halves positive) — 4th appearance of the trend-firmness
signal. RSI depth alone: L2-fill rate FLAT ~50-53% at every threshold (20/80
slightly worse) — "enter deeper so trades don't go adverse" measured
nonexistent. BEST CELL OF THE INVESTIGATION: RSI 30/70 + gap>=0.6%:
0-fee PF 1.176, +$7.76/trade (~6.5bp), both halves positive — and STILL
−$19.81/trade with real fees (PF 0.654). The maximum extractable entry edge
recovers ~43% of its own trading costs. Entry optimization is closed at its
global optimum.

BB+SMA SWEEP — new global best 5m cell (2026-06-12): BB(20,2.5σ) touch
entries + 15m-trend alignment + exit at the OPPOSITE band (rsiscalp chassis,
honest engine): 0-fee PF 1.348, +$14.95/trade (~12.6bp gross), BOTH halves
positive (+$18.3/+$10.1 per trade) — the largest honest gross edge of the
investigation; three real whispers (band depth, trend firmness, wide dynamic
target) composed instead of subtracting. STILL −$13.36/trade at taker
(blended RT cost ~$28 — wide targets hold longer/DCA more). First cell where
fantasy-maker execution would flip positive on paper (~+$9/trade) — but
resting-order adverse selection killed every prior such rescue
([[resting-orders-dont-rescue-rsiscalp]]); needs an EXCHANGE-mode sim before
any belief. Entry-surface optimum updated from RSI30/70+gap0.6 to this cell.

L1/L2 DECOMPOSITION — the conditioning fallacy quantified (2026-06-12):
baseline honest trades split by whether L2 filled: L1-only 4,728 trades,
WR 98.2%, +$202K; L2-filled 5,080 trades, WR 22.7%, −$448K. Looks like
"just keep the L1 trades" — but L1-only is an OUTCOME label, not an entry
condition: a trade stays L1-only precisely because price never went −0.5%
against it. Selecting it = selecting on the future. Harvest attempts measure
exactly that: "L2 as stoploss" (exit at −0.5% instead of averaging) PF 0.971
0-fee / −$196K real; remove-L2 PF 0.973 / −$208K — both in the dead band,
and both GROSS-WORSE than baseline (1.041): averaging down actually improves
gross expectancy; L2 trades are where the entries' wrongness is expressed,
not its cause. The 98.2% L1-only WR is the purest form of the dashboard
illusion. Don't re-split.

REVIEW STACK #4 RUN VERBATIM (2026-06-12): 5m SMA-ADX(14)<25 + vol-conditional
RSI 30/70 + exhaustion-wick>50% + SL1% + no time-SL ("equity will transform").
ADX<25 ALONE is the largest sub-cost whisper found to date: 0-fee PF 1.083 /
+$3.42/trade gross (vs baseline 1.041/+$1.74) — but decaying (0-fee gross
2023+ only +$1.5K of +$20K) and still −$23.44/trade with fees. The FULL stack
is WORSE than its own ADX part (0-fee PF 1.037; real −$25.90/trade < baseline
−$25.02) — the wick filter destroys the ADX gain, passes 0.8% of bars, and
its 0-fee gross is NEGATIVE in 2023+. No transform; pacing plus a decaying
whisper. Fifth confirmation that confirmation-stacking subtracts.

STRUCTURE & INDICATOR STOPS (2026-06-12): (a) "SL below 24h support/above
resistance" (touch-stop at the level, skip trades with no protective level)
is the WORST stop placement measured: 0-fee PF 0.974-0.990 (below baseline
1.041) — the obvious S/R levels are exactly where wicks sweep (78% of breaks
recover, see BREAKOUT-CUT). +TP=2R version worse still (PF 0.902). (b) BB-band
stop ≈ baseline. (c) EMA50-anchored stops (stop rides the line; only trade
when the line is protective-side): 15m PF 1.169 / 1h **PF 1.172, +$5.72/trade
0-fee, both halves positive — the LARGEST gross edge of the entire
investigation (≈4.8bp/trade)**. Mechanism: the skip rule smuggles in a 1h
trend filter and the line-exit lengthens holds — it morphs the scalper into a
1h trend-pullback system (the #4 gradient, not stop magic). Real taker fees:
still −$12.13/trade (PF 0.700, best real-fee cell ever, still bleeding);
blended realistic maker/taker ≈ 5-6bp vs 4.8bp edge — under the line even in
the best case. The gradient's message repeated: anchoring to slower
structures helps exactly in proportion to how much it stops being a scalper.

BREAKOUT-CUT EXIT (2026-06-12): close the whole position when a bar CLOSES
beyond the prior 6h/12h/24h extreme against it. Every cell worse: 0-fee PF
1.041 -> 1.004/1.031/1.040; real-fee totals -$246K -> -$248..253K (more
round trips). The 6h cut fired 4,499 times and BE-DCA recoveries collapsed
3,000 -> 649 — i.e. ~78% of "confirmed breakouts" against an open position
were false breaks that would have recovered to breakeven. Cutting on
breakout realizes the false breaks to dodge the real ones: same identity as
the bag-SL and the weakness-rectangle entry stats. Closed.

## 3. Wrappers don't create edge — proven by identity and simulation

Hedge ("open L+S, close winner"), martingale, grid, DCA are exposure-path
wrappers: P&L = exposure x price moves - fills x costs. Hedge-cycle sim:
+$330 realized "wins" mirrored by -$329 in open legs (net = -fees).
Martingale 2x at 5x lev: 99.5% WR, liquidated in 16 days (2021-05-19).
Commercial bots (3commas/Pionex/Tafabot/Bybit bot cards) showing 100% win
rates use realized-only accounting — same fiction as #1.

POPULAR-SPEC MARTINGALE (2026-06-12, backtest/martingale_popular.py): Bybit
Futures-Martingale spec (cost-multiplier SOs, TP-from-avg as market order,
no SL), 144 configs (dev 1-3% x mult 1.5/2 x SO 7/10 x TP 1-2% x ALWAYS/
RSI14<30 gate x 1x/3x), BTC 1h 2019-2026, full ladder honestly capitalized:
EVERY config shows 100% closed-round WR. At 3x: nearly all liquidated
2020-03-12 (COVID) or 2022-06-14 -> $0. At 1x spot: never liquidates, but
BEST config ($5K -> $33.1K) UNDERPERFORMS BTC buy-hold ($37.8K) with the
same ~75% MTM drawdown, and every config ends holding an open bag — it is
buy-hold with extra fees. RSI<30 entry gate: fewer rounds, LESS profit
(best $14.5K), similar DD — gating doesn't protect, it just trades less.
The 100% WR + liquidation/bag pattern is the wrapper identity made visible.
BAG-AVOIDANCE MATRIX (backtest/bag_avoidance.py, engine hand-validated by
backtest/martingale_validate.py 4/4): round-SL realizes the bag -> final
collapses ($33.1K -> $1.1-4.6K, WR 96-97% w/ -$900 worst rounds = rsiscalp
profile); 200d-SMA trend gate -> $25.7K, bag/DD persists (open bags still
ride crashes); all protections combined (RSI gate + SMA gate + SL) -> DD 15%
but only ~+27%/6.6y — strictly dominated by momo_v1 (+140%/5.1y, DD 21%).
The bag IS the strategy's loss half; it can be realized or reduced, never
avoided while keeping the wins.
COVERAGE-vs-INCOME (2026-06-12, same engine): the "BTC max 4%/day" sizing
premise is false — −4% days every ~3 weeks (157/2461), worst day −40%
(intraday −45%), worst week −47%, worst month −53%, bear −77%, 846 days
underwater. Ladders sized to actually cover this (46-80% coverage, dev 4-8%
x SO 15-20, low mult) DO survive 6.8y with 100% WR, no bag, no liq — and
earn $169-$734 TOTAL on $5K (0.5-2%/yr) because covering deep moves shrinks
the base order to $22-57 (base = capital/units, units grows geometrically
with coverage). Safety and income are arithmetically exclusive; no ladder
geometry is both safe and worth running.
3x GOAL SWEEP (2026-06-12, backtest/best_martingale_3x.py): 480 configs
(dev 1-8% x mult 1.1-2 x SO 5-20 x TP 1-3% x ALWAYS/RSI), 3x leverage:
61% LIQUIDATED. Best survivor (dev 8%/mult 1.1/SO 15/TP 3%, ALWAYS):
0.0596%/day at 79.7% MTM DD, 13/15 rungs used (2 from death) — BELOW
buy-hold (0.0837%/day) and 17x short of 1%/day. The 1%/day-via-martingale
question is measured and closed at every leverage.

## 4. Timeframe gradient — the only edge is at daily scale

Structure-break system (EMA50 bias + N-day breakout + ratchet trail, SL-flip)
tested at every timeframe, real fees, 2019/20-2026:
  5m/15m/1H fast: PF 0.88-1.13 (dead) | 4H: 1.01-1.24 (marginal)
  1D: PF 1.35-1.85 full-sample, beats buy-hold with half the DD, +2022 bear.
Sub-daily cells only survive with multi-day lookbacks (= daily in disguise).
WALK-FORWARD (fit 2019-23, blind 2024-26): MARGINAL — fit-selected cell OOS
PF 1.05; only slowest cells (N>=55) clearly positive OOS (PF 1.16-1.70).
Edge is real, small, decaying. Deploy slow + small or not at all.

## 5. What is actually real

- Funding harvest: long spot + 1x perp short, income = funding (~8% APR at
  2026-06-11 rates, paid 8-hourly). External cash flow, no prediction,
  no liquidation at 1x. The only income here that isn't a backtest.
- Slow daily structure-break (N>=55) as a small satellite (see #4).
- Realistic total on $5K: roughly $30-60/month. Anything promising more
  has so far always contained a fill fiction or fee blindness.

## 6. The 1%/day question — leverage frontier measured (2026-06-11)

The maximum honest daily growth rate on BTC was measured by leverage-scaling
the one honest survivor (MOMO v1 rules on perps, funding paid while long,
8% APR funding harvest on flat days, liquidation on intraday lows, next-open
fills — backtest/momo_lev_sweep.py):
  L=1: 0.094%/day | L=2: 0.148%/day (maxDD 54%) | L=3: 0.188%/day (DD 68%)
  L=5: 0.238%/day (DD 85%) | L>=6: LIQUIDATED.
  Worst per-trade adverse excursion 17.8% vs 19.5% liq buffer at 5x —
  survival at 5x was luck, not design. Intraday stops do NOT unlock higher
  leverage (8x+ gaps through the stop to liquidation) and reduce g/day at
  survivable leverage. On the 2021+ window (excl. 2020 bull) rates are
  ~40% lower (L=2: 0.099%/day). HONEST CEILING: ~0.10-0.15%/day deployable
  (L=2), ~0.24%/day at uninsurable risk. 1%/day is 4-10x beyond the ceiling
  of the best known honest edge; combined with the 5m sweep (zero gross edge,
  150 combos), the 1%/day goal is conclusively unachievable on BTC alone.

## 7. Protective exits don't improve momo_v1; protection must be account-level

14 protective overlays on momo_v1 (trail 10/15/20%, chandelier ATR 2-4x,
hard SL 5/8/10%, profit ratchets, combos; immediate + fresh-trigger re-entry)
tested honestly with IS(≤2023)/OOS(2024+) split — backtest/momo_protect_overlay.py:
NONE beat baseline on both halves. Worst single trades stay ~-10% under every
overlay because momo's losses are entry gaps, not given-back profits. Best
variant (ratchet 20/10: once +20%, exit at +10%) improved IS only and never
triggered OOS — not deployed (would contaminate the pre-registered live test
for an in-sample-only benefit). Conclusion: per-trade exits can't cut momo's
DD without cutting its edge; its 21-34% DD is the price of the +139% return.

MTF EXECUTION OVERLAY (2026-06-12, backtest/momo_mtf_overlay.py): gating momo
with faster-TF trend filters (in market only while momo AND 1h-EMA20/50 /
1h-SMA50 / 15m-EMA20/50), perp costs+funding, L=2/3/5: the protection WORKS
mechanically — worst MAE 17.7% -> 5.9% — but every step down in gate speed
multiplies trades (22 -> 99 -> 390 -> 425) and whipsaw+fees eat more than the
protection saves: g/day at L=2 falls 0.146% (base) -> 0.091% (1h EMA) ->
0.073% (1h SMA50) -> 0.035% (15m). Lower MAE does NOT unlock leverage: gated
L=5 is worse than ungated L=2 everywhere; 15m gate at L=5 is NEGATIVE (97%
DD). The timeframe gradient (#4) operates inside hybrids too — fast layers
subtract in proportion to how often they act. Don't bolt intraday filters
onto daily strategies.

For the no-edge rsiscalp bots the protection IS account-level (deployed
2026-06-12, bot_rsiscalp_v3.py): equity-ratchet kill-switch — hard floor at
INITIAL×0.90 ($4,500); once peak ≥ INITIAL×1.04 the floor ratchets to
peak×0.97, monotonic; when FLAT and balance ≤ floor, entries halt PERMANENTLY
(state.halted_reason; manual edit to resume). Plus the existing 4%/day loss
breaker. Worst case for the 2-week truth experiment is bounded at -10%;
any +4% peak converts to a locked-in profit floor.

MTM BASKET STOP (deployed 2026-06-12, RSISCALP_MTM_STOP_PCT=0.04): user
observed R:R ~1:3.5 (wins ~+$60, losing baskets -$200+, worst -$818 — the
balance-based guards above are realized-only and blind to open-basket
unrealized loss; the 6-bar BE-wait window has NO stop at all). Honest A/B
(backtest/mtm_guard_ab.py, live_faithful engine, 2021-2026): closing the
basket at -4%-of-balance unrealized cut worst trade -$818 -> -$270/-$380,
avg loss -10-13%, PF 0.59 -> 0.64 (v2.2 REAL), never worse in any mode.
A 2% cap was TOO TIGHT (more stop-outs, worse PF); a +2% unrealized
PROFIT-LOCK never helped (v2.1: never triggers — TPs are closer; v2.2: cuts
the 1% TP winners early, less profit). Damage control only — PF stays <1.

## 8. Live state (as of 2026-06-12)

v2.1 + v2.2 paper bots on GCP btc-bot-eu (cron, 1-min ticks) run with honest
booking, 0.055% fees, SL env fixed, fresh $5K states (old histories archived
as data/v2.*/state_fictional_backup_20260611_1318.json). They are a 2-week
truth experiment, expected to bleed slowly per #2. Paper still omits
slippage, funding, intra-minute moves — reads slightly BETTER than real.
Kill-switch live on both (see #7); as of 2026-06-12 v2.2's floor has already
ratcheted to $5,148 (peak $5,308). momo_v1 runs unmodified (validated rules,
no overlay) at $5K flat.

## 9. QFL / "crypto base scanner" crack-buying is dead on majors (2026-06-12)

backtest/qfl_base_scan.py: pivot-low bases (W=12h window, 3% bounce confirm,
no lookahead), resting-limit buys 3/5/8% below the base, TP back at the base,
SL 8/15%, 14d time-stop, maker/taker fees — BTC/ETH/SOL/BNB 1h, 2019-2026,
IS/OOS. ALL 24 asset x cell combos NEGATIVE full-sample (-0.6 to -4.1%/trade
net; most compound to ~-100%). Fees are irrelevant at this scale — the loss
is structural: +3-8% bounces vs -8/-15% stops is negative skew, and in trend
breaks (2022) cracked bases keep cracking. Same profile as rsiscalp (high WR,
rare big losses), one timeframe up. Base DETECTION engine works and is
reusable (find_bases); the crack-BUYING strategy is falsified on majors.
Untested: illiquid small-cap alts (the original QFL hunting ground) — those
need different data and carry exchange/liquidity risk a paper test can't price.

FULL BTC SWEEP (2026-06-12, backtest/btc_base_sweep.py): 270 cells — pivot
window {6,12,24}h x crack {1,2,3,5,8}% x TP {0.5,1,2,3,5%, full-revert} x SL
{4,8,15}%, IS(..2023)/OOS(2024..) discipline. IS-POSITIVE CELLS: 0 of 270.
POSITIVE IN BOTH HALVES: 0. The 20 OOS-positive cells are all IS-negative
with N=11-65 (noise); best IS cell (crack 1%/TP 1%/SL 15%, WR 90%) still
loses -0.61%/trade and compounds -94%. IS-winner zero-fee gross -0.48%/trade:
FEES ARE NOT THE ISSUE — the signal is. There is no best base-crack strategy
on BTC; the family is closed at every parameterization. Do not re-sweep.

## 10. Pump-fade shorts: first short-side signal with excess edge (2026-06-13)

backtest/short_gainers_listings.py — event study, ALL 582 Bybit USDT perps,
daily klines (≤1000 bars ≈ Oct 2023-Jun 2026), costs 0.15%/round-trip.
SHORT at the close of any day a coin gained ≥+30%, exit close-to-close:

  exit +1d: N=1546 mean +5.4% / med +4.7% / win 62%
  exit +3d: N=1528 mean +13.3% / med +7.6% / win 63%
  CONTROL (short any coin, any day): +1d +0.27%, +3d +1.23% — so the pump
  signal itself carries +5.1% (1d) / +12.0% (3d) EXCESS, not just alt-bear beta.
  By quarter (3d): 9/12 positive; weakest = alt rallies 2024Q2/Q3 (-2/-3%).

This is DIFFERENT from the 9 dead trend-shorts (those were on majors).

PORTFOLIO VERDICT (2026-06-13, backtest/pump_fade_portfolio.py +
pump_fade_confirm.py): FALSIFIED AT PORTFOLIO LEVEL. The per-event
arithmetic mean does not survive compounding, intraday tails, or funding:
  - Blind short, $5k / 10% margin per pos / max 10 concurrent / isolated:
    ALL 16 configs (lev 1-5x, exit 1/3d, all-perps + liquid-only) LOSE.
    All-perps 1x/1d: -78%, 41 LIQUIDATIONS AT 1x (intraday +100% squeezes),
    maxDD -82%. Best blind config (liquid 1x/1d): -25%, maxDD -38%.
  - FUNDING (actual Bybit history, 240 events): mean -1.02%/d of notional
    AGAINST the short (median +0.03% but p5 -5.8%/d — the same squeezes
    that pump also charge shorts). 3d hold: mean -2.5%.
  - Momentum-confirm entries (user idea: wait for stall + candle):
    RED-candle confirm and BREAKDOWN (close < prior low) cut tails
    (2x-liq-touch 8%->3%, worst -75%->-52%) but also cut edge (mean
    +5.1%->+3.2% at 1d). Best overall config (breakdown + 1d + 2x):
    -1% before funding => clear loser after funding. Momo-exit (first
    green candle) is worse everywhere (median negative).
  - Root cause: mean>>median with p5 ~ -25-30% = heavy-tailed payoff;
    geometric compounding of clustered correlated events turns a +3-5%
    arithmetic per-trade mean into a negative equity curve. Same lesson
    as the martingale family, mirrored.
CLOSED: pump-fade shorting (blind or candle-confirmed, daily TF) is not
deployable. Untested: intraday (15m/1h) confirmation timing — but funding
and tail structure are unchanged, so expectations are low.

## 11. YouTube "10-year Bollinger" guru strategy: zero gross edge (2026-06-13)

backtest/bb_guru_mtf.py — faithful mechanization of the video strategy
(1h SMA20-slope bias -> 5m pullback to HIGH/LOW-based BB(20,2σ-EMA) +
hammer-candle confirm, with-trend only, SL at signal wick, pre-registered
exits 1R/2R/mid-band + 12h time stop, SL-first pessimistic, real costs):
BTC 5y (12.5K trades), ETH+SOL 2.3y (6.3K each): EVERY variant, EVERY year,
BOTH sides negative. PF 0.26-0.42, compounded -100% everywhere.
KEY FINGERPRINT: avg net -0.15%/trade == exactly the 0.15% round-trip cost
-> gross edge is 0.00%. The signal is a coin flip; the video's "90% of band
touches revert" is true but worthless (reversion magnitude ~ noise, and the
10% that don't revert pay for the 90%). Consistent with FINDINGS #4 (zero
gross edge at 5m), candle-pattern failures, and BB%B decay. The video's
business model is the broker-affiliate link, not the strategy.
Untested: gold/NASDAQ (his claimed markets), discretionary execution.

## 12. Jesse cross-engine validation: v2.1/v2.2 bleed confirmed (2026-06-13)

Independent engine test (user request): as-deployed v2.1/v2.2 ported to
Jesse 2.3.4 (backtest/jesse_port/, .venv-jesse py3.11) — full ruleset from
the run scripts (with-trend/counter-trend, gap, ATR, 2-leg DCA, BE-wait 6,
L2 trail, trend-line stop, hard 6h time-SL, 15-min loss cooldown), 5m
decisions with 1m-candle order execution, 90 days of real Bybit 1m data
(2026-03-15..06-13), 5x futures, 0.055% fees:
  v2.1: 200 trades, WR 33%, PF 0.51, -43.9%, maxDD -44%, fees $2,168
  v2.2: 317 trades, WR 55%, PF 0.59, -70.5%, maxDD -73%, fees $3,753 (on $5K!)
Matches the honest harness (PF 0.60/0.67) and the rsiscalp_v6.pine HONEST
mode — FOURTH independent engine, zero shared code with our harness.
Jesse is mildly OPTIMISTIC vs the live polling bot (stop fills at stop price,
no slippage), so reality is worse. Fees alone exceed half the starting
account in 90 days. Supports retiring v2.1/v2.2 at the 2026-06-25 review.
REUSABLE INFRA: .venv-jesse + jesse_port/{fetch_1m,strategy_v2,run_jesse_v2}.py
— headless jesse.research.backtest harness with real 1m Bybit candles, for
any future strategy cross-check.

## File map for analysis

- bot/bot_rsiscalp_v3.py + bot/core_rsiscalp.py — live strategy (env
  overrides in scripts/run_v2.*.sh take precedence over code defaults!)
- Honest harness + all experiment scripts: git commit 5cad355
  (e.g. `git show 5cad355:backtest/live_faithful.py`)
- Data: data/cache/BTCUSDT_{5m,15m,1h,4h,1d}.csv
- TradingView port: backtest/rsiscalp_v6.pine (LEGACY vs HONEST fill modes)

## Checklist for any NEW backtest (non-negotiable)

1. Signals on closed bars only; fill next bar open.
2. Stop fills = worse(stop price, bar open). Never book a stop at its own
   price when the bar opens beyond it.
3. Same-bar DCA-fill + exit: defer exit (no wick-order lookahead);
   TP+SL same bar -> take the SL (pessimistic).
4. Real fees both sides (taker 0.055%) + slippage on market fills.
5. Track mark-to-market equity and liquidation, not just closed balance.
6. Year-wise results + walk-forward before believing any total.
