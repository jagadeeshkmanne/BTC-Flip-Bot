#!/usr/bin/env python3
"""predict_entry_skip.py — can a model, given EVERYTHING at signal time,
predict which of THIS bot's trades to skip? (user 2026-06-12)

The strongest version of entry-avoidance: instead of one indicator, feed a
gradient-boosted classifier every feature available at the entry bar and let
it find any combination that flags losers. Honest protocol:
  - trades from the honest engine (real fills, fees) on the deployed v2.2
  - features strictly from the signal bar (open_i - 1) and prior — no leakage
  - TRAIN on trades through 2022-12-31, pick the skip-threshold on TRAIN ONLY
  - evaluate skipping on 2023+ trades it has never seen
If the OOS skip improves $/trade AND total $, the warning exists. If AUC ~0.5,
the candles do not contain it — closing the question with the heaviest tool.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score
import fresh_honest as fh

SPLIT = pd.Timestamp("2023-01-01")

bt = fh.prep()
r = fh.run(bt, tp_dca=0.0100, time_sl=144, fill="honest", fee=0.00055,
           slip=0.0002, sl_l1=0.006, fixed_cap=True)
tr = r["trades"]
print(f"honest v2.2 trade stream: {len(tr):,} trades")

RSI = bt["rsi"].values; ATR = bt["atr_pct"].values; GAP = bt["gap"].values
TRN = bt["trend"].values; RP = bt["rp24"].values
TS = bt["timestamp"]
HOUR = TS.dt.hour.values; DOW = TS.dt.dayofweek.values
C = bt["close"].values
ret12 = pd.Series(C).pct_change(12).values      # 1h momentum
ret72 = pd.Series(C).pct_change(72).values      # 6h momentum
vol24 = pd.Series(C).pct_change().rolling(288).std().values

rows, nets, when = [], [], []
prev_net = 0.0
for net, reason, t_exit, open_i, side in tr:
    s = open_i - 1                              # signal bar (closed before entry)
    rows.append([
        side, RSI[s], abs(RSI[s] - (35 if side == 1 else 65)),  # signal depth
        ATR[s], GAP[s] * 100, TRN[s] * side,    # with/against trend
        RP[s], HOUR[s], DOW[s],
        ret12[s] * 100 * side, ret72[s] * 100 * side, vol24[s] * 100,
        prev_net,                                # last trade's outcome (regime memory)
    ])
    nets.append(net); when.append(pd.Timestamp(t_exit))
    prev_net = net

X = np.array(rows); y = (np.array(nets) > 0).astype(int)
nets = np.array(nets); when = pd.Series(when)
m_tr = (when < SPLIT).values; m_te = ~m_tr
print(f"train {m_tr.sum():,} trades (..2022) | test {m_te.sum():,} (2023..) | "
      f"base WR train {y[m_tr].mean()*100:.1f}% test {y[m_te].mean()*100:.1f}%")

clf = HistGradientBoostingClassifier(max_iter=300, max_depth=3,
                                     learning_rate=0.05, random_state=7)
clf.fit(X[m_tr], y[m_tr])
p_tr = clf.predict_proba(X[m_tr])[:, 1]
p_te = clf.predict_proba(X[m_te])[:, 1]
print(f"AUC: train {roc_auc_score(y[m_tr], p_tr):.3f} | "
      f"TEST {roc_auc_score(y[m_te], p_te):.3f}  (0.500 = no information)")

feats = ["side", "rsi", "rsi_depth", "atr", "gap", "with_trend", "rp24",
         "hour", "dow", "mom1h", "mom6h", "vol24h", "prev_net"]
pi = permutation_importance(clf, np.nan_to_num(X[m_te]), y[m_te],
                            n_repeats=5, random_state=7, scoring="roc_auc")
imp = sorted(zip(feats, pi.importances_mean), key=lambda x: -x[1])
print("top features (OOS permutation): " + "  ".join(f"{f}:{v:+.3f}" for f, v in imp[:5]))

# economic test: choose skip threshold on TRAIN to maximize train total $,
# then apply unchanged to TEST
best_thr, best_tot = None, -1e18
for thr in np.percentile(p_tr, np.arange(5, 60, 5)):
    tot = nets[m_tr][p_tr >= thr].sum()
    if tot > best_tot:
        best_tot, best_thr = tot, thr
keep_te = p_te >= best_thr
print(f"\nskip rule fixed on TRAIN (keep if P(win) >= {best_thr:.3f}, "
      f"skips {100*(1-keep_te.mean()):.0f}% of TEST trades):")
print(f"  TEST baseline:    {m_te.sum():,} trades  avg ${nets[m_te].mean():+.2f}  "
      f"total ${nets[m_te].sum():,.0f}")
print(f"  TEST with model:  {keep_te.sum():,} trades  avg ${nets[m_te][keep_te].mean():+.2f}  "
      f"total ${nets[m_te][keep_te].sum():,.0f}")
print(f"  trades the model skipped were worth: ${nets[m_te][~keep_te].sum():,.0f} "
      f"(negative = it correctly avoided losses)")
