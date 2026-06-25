import sys; sys.path.insert(0,'/Users/jags/Desktop/BTC-Flip-Bot/scripts'); import bt_helpers as bt
import numpy as np, pandas as pd

def squeeze_sig(df, pctl, brk, bbwin=20, pctl_win=240, look=40):
    c=df['close']
    lo,mid,hi=bt.bbands(c,bbwin,2.0); bw=(hi-lo)/mid
    sq=(bw<bw.rolling(pctl_win).quantile(pctl)).rolling(look).max().fillna(0).astype(bool)
    hh=c.rolling(brk).max()
    return ((c>=hh)&sq&(c>bt.ema(c,200))).fillna(False).values

print("FULL system: squeeze(pctl)+breakout(brk)+EMA200 trend, tp8/sl4/trail3x/mh30, long-only, TAKER")
print("Honest: IS-select (pctl,brk) on IS 60%, report OOS 40%")
for coin in ['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT']:
    df=bt.load(coin,'4h'); best=None
    for pctl in [0.20,0.25,0.30]:
        for brk in [40,55,70]:
            li=squeeze_sig(df,pctl,brk)
            eq,n,wr,pf=bt.backtest_tpsl(df,li,tp=0.08,sl=0.04,trail_atr=3.0,max_hold=30)
            is_eq,oos_eq=bt.oos_split(eq,0.6)
            m_is=bt.metrics(is_eq); m_oos=bt.metrics(oos_eq)
            if best is None or m_is[2]>best[0]: best=(m_is[2],pctl,brk,m_oos[2],m_oos[0],m_oos[1],n,wr,pf)
    bh=bt.buyhold(df); ob=bt.oos_split(bh,0.6)[1]; bhrdd=bt.metrics(ob)[2]
    win="WINS" if best[3]>bhrdd else "loses"
    print(f"{coin}: IS-best pctl{best[1]} brk{best[2]} -> OOS rDD {best[3]:.3f} (cagr {best[4]:.3f} dd {best[5]:.3f} trd {best[6]} wr {best[7]:.0f} pf {best[8]:.2f}) | BH {bhrdd:.3f} -> {win}")
