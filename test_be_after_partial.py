"""Test: after partial TP fires, move SL to break-even (entry + small buffer for fees)."""
import os, numpy as np, pandas as pd
ROOT=os.path.dirname(os.path.abspath(__file__))
START=10_000.0; LEV=2.0; FEE=0.0004; SLIP=0.0003
SL_MAX=0.025; SL_BUF=0.001; DD_HALT=0.25; DD_BARS=168
CD=2; SD_CD=24; ATR_MA=50; VOL_MA=20; VOL_R=1.5
FW=1; FSC=0.015; FSR=10; FTS=24

def ema(s,n): return s.ewm(span=n,adjust=False).mean()
def rsi(s,p=14):
    d=s.diff(); g=d.clip(lower=0).rolling(p).mean(); l=-d.clip(upper=0).rolling(p).mean()
    return 100-100/(1+g/l)
def macdc(s):
    ef=s.ewm(span=12,adjust=False).mean(); es=s.ewm(span=26,adjust=False).mean()
    ln=ef-es; return ln,ln.ewm(span=9,adjust=False).mean()
def atr_c(df,p=14):
    hl=df["high"]-df["low"]; hc=(df["high"]-df["close"].shift()).abs(); lc=(df["low"]-df["close"].shift()).abs()
    return pd.concat([hl,hc,lc],axis=1).max(axis=1).rolling(p).mean()
def maph(htf,rule,bts,col):
    h=htf.copy(); h["ct"]=h["timestamp"]+pd.Timedelta(rule)
    s=pd.Series(h[col].values,index=h["ct"].values); s=s[~s.index.duplicated(keep="last")]
    return s.reindex(s.index.union(bts)).sort_index().ffill().reindex(bts).fillna(0).values

src=pd.read_csv(os.path.join(ROOT,"data","cache","BTCUSDT_15m_1825d.csv"))
src["timestamp"]=pd.to_datetime(src["timestamp"]).dt.tz_localize(None)
for c in ["open","high","low","close","volume"]: src[c]=src[c].astype(float)
src=src.sort_values("timestamp").reset_index(drop=True)
df=src.set_index("timestamp").resample("1h").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna().reset_index()
df4=src.set_index("timestamp").resample("4h").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna().reset_index()
dd_=src.set_index("timestamp").resample("1D").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna().reset_index()
df["rsi"]=rsi(df["close"]); df["ml"],df["ms"]=macdc(df["close"])
df["atr"]=atr_c(df); df["atr_ma"]=df["atr"].rolling(ATR_MA).mean(); df["hv"]=df["atr"]>df["atr_ma"]
df["vs"]=df["volume"].rolling(VOL_MA).mean(); df["vok"]=df["volume"]>VOL_R*df["vs"]
b=(df["close"]-df["open"]).abs(); pb=b.shift(1)
df["bull"]=(df["close"].shift(1)<df["open"].shift(1))&(df["close"]>df["open"])&(df["close"]>=df["open"].shift(1))&(df["open"]<=df["close"].shift(1))&(b>pb*1.0)
df["bear"]=(df["close"].shift(1)>df["open"].shift(1))&(df["close"]<df["open"])&(df["open"]>=df["close"].shift(1))&(df["close"]<=df["open"].shift(1))&(b>pb*1.0)
df4["rsi4"]=rsi(df4["close"]); df4["cf"]=np.where(df4["rsi4"]>50,1,np.where(df4["rsi4"]<50,-1,0))
dd_["e50"]=ema(dd_["close"],50); dd_["bs"]=np.where(dd_["close"]>dd_["e50"],1,np.where(dd_["close"]<dd_["e50"],-1,0))
bts=pd.DatetimeIndex(df["timestamp"])
bias_d=maph(dd_,"1D",bts,"bs").astype(int); cf4=maph(df4,"4h",bts,"cf").astype(int)

def run(name, move_sl_to_be=False, be_buffer_pct=0.002):
    """move_sl_to_be: after partial TP fires, move SL to entry + buffer (BE)"""
    cap=START; pos=0; ep=0; sl=0; part=False; cd_=0; halt=-1
    lsl=ssl=-10**9; peak=cap; mdd=0; tr=[]; h=0; W=L=0
    is_flip=False; entry_bar=0; pending_side=0; pending_bar=-1; pending_ref=0
    hi=df["high"].values; lo=df["low"].values; cl=df["close"].values; at=df["atr"].values
    be_triggered = 0
    for i in range(100,len(df)):
        r=df.iloc[i]; p=cl[i]; a=at[i]
        if i<halt: continue
        if cd_>0: cd_-=1
        l1=(r["rsi"]>45) and (r["ml"]>r["ms"]) and bool(r["bull"]) and r["hv"] and r["vok"]
        s1=(r["rsi"]<55) and (r["ml"]<r["ms"]) and bool(r["bear"]) and r["hv"] and r["vok"]
        lf=l1 and bias_d[i]==1 and cf4[i]==1
        sf=s1 and bias_d[i]==-1 and cf4[i]==-1
        if lf and (i-lsl)<SD_CD: lf=False
        if sf and (i-ssl)<SD_CD: sf=False

        if pending_side != 0 and pos == 0 and (i - pending_bar) >= FW:
            lb_start = max(0, i - FSR)
            if pending_side == -1:
                sh=max(hi[lb_start:i+1]); sl_new=min(sh*(1+SL_BUF), pending_ref*(1+FSC))
                pos=-1; ep=p; sl=sl_new; is_flip=True; entry_bar=i
            else:
                sl_low=min(lo[lb_start:i+1]); sl_new=max(sl_low*(1-SL_BUF), pending_ref*(1-FSC))
                pos=1; ep=p; sl=sl_new; is_flip=True; entry_bar=i
            pending_side = 0

        def cp(px,rs):
            nonlocal cap,pos,cd_,W,L,lsl,ssl,part,is_flip,pending_side,pending_bar,pending_ref
            rem=0.7 if part else 1.0
            pmp=((px-ep)/ep if pos==1 else (ep-px)/ep)
            net=pmp*LEV*rem-(FEE+SLIP)*2*LEV*rem
            cap*=(1+net); tr.append(net*100)
            if net>0: W+=1
            else: L+=1
            was_flip=is_flip
            if rs=="SL":
                if pos==1: lsl=i
                else: ssl=i
                if not was_flip:
                    pending_side=-1 if pos==1 else 1
                    pending_bar=i; pending_ref=px
            part=False; pos=0; cd_=CD; is_flip=False

        # Partial TP
        if pos!=0 and not part and not is_flip:
            d=abs(ep-sl)
            if d>0:
                rm=((p-ep)/d if pos==1 else (ep-p)/d)
                if rm>=5.0:
                    f=0.3; pmp=((p-ep)/ep if pos==1 else (ep-p)/ep)
                    cap*=(1+pmp*LEV*f-(FEE+SLIP)*2*LEV*f); part=True
                    # After partial TP: optionally move SL to BE
                    if move_sl_to_be:
                        if pos == 1:
                            sl = ep * (1 + be_buffer_pct)  # BE + buffer (covers fees)
                        else:
                            sl = ep * (1 - be_buffer_pct)
                        be_triggered += 1

        if is_flip and pos!=0 and (i-entry_bar)>=FTS:
            cp(p,"TIME"); continue
        if pos==1:
            if lo[i]<=sl: cp(sl,"SL")
            elif sf: cp(p,"FLIP")
        elif pos==-1:
            if hi[i]>=sl: cp(sl,"SL")
            elif lf: cp(p,"FLIP")
        elif cd_==0 and pending_side == 0:
            if pd.isna(a) or a<=0: pass
            elif lf:
                pos=1; ep=p; part=False; is_flip=False; entry_bar=i
                pl=min(r["low"],df["low"].iloc[i-1])
                sl=max(pl*(1-SL_BUF),ep*(1-SL_MAX))
            elif sf:
                pos=-1; ep=p; part=False; is_flip=False; entry_bar=i
                ph=max(r["high"],df["high"].iloc[i-1])
                sl=min(ph*(1+SL_BUF),ep*(1+SL_MAX))

        peak=max(peak,cap); dd=(cap-peak)/peak*100 if peak>0 else 0
        if dd<mdd: mdd=dd
        if dd<=-DD_HALT*100:
            halt=i+DD_BARS; h+=1
            if pos!=0: cp(p,"SL")
            peak=cap

    yrs=(df["timestamp"].iloc[-1]-df["timestamp"].iloc[0]).days/365.25
    n=len(tr); wr=W/max(W+L,1)*100
    gw=sum(t for t in tr if t>0); gl=abs(sum(t for t in tr if t<=0))
    pf=gw/max(gl,1e-9); cg=((cap/START)**(1/yrs)-1)*100 if cap>0 else float("nan")
    calmar = cg / abs(mdd) if mdd != 0 else 0
    print(f"{name:<55}{cap:>11,.0f}{cg:>+7.1f}%{mdd:>+7.1f}%{calmar:>6.2f}{n:>5}{wr:>6.1f}%{pf:>7.2f}  BE-moves:{be_triggered}")

print(f"{'Variant':<55}{'Final':>11}{'CAGR':>8}{'DD':>8}{'Calm':>6}{'Trd':>5}{'WR':>7}{'PF':>7}")
print("─"*107)
run("V6 current (SL stays put after partial TP)", move_sl_to_be=False)
run("Move SL to BE+0.1% after partial TP",         move_sl_to_be=True, be_buffer_pct=0.001)
run("Move SL to BE+0.2% after partial TP (fees)",  move_sl_to_be=True, be_buffer_pct=0.002)
run("Move SL to BE+0.5% after partial TP",         move_sl_to_be=True, be_buffer_pct=0.005)
run("Move SL to BE+1% after partial TP",           move_sl_to_be=True, be_buffer_pct=0.010)
