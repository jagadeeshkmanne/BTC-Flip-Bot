import clsx from 'clsx';
import { TrendingDown, TrendingUp, Check, X, Clock, Activity } from 'lucide-react';
import type { BotStatus, StrategyId } from '@/types/bot';
import { useTickerStore } from '@/hooks/useBtcStream';

interface Props {
  status?: BotStatus;
  strategy: StrategyId;
}

export function PositionPanel({ status, strategy }: Props) {
  if (!status) {
    return <div class="card-elev py-12 text-center text-text-muted text-sm">Loading…</div>;
  }
  if (status.position) {
    return <ActivePosition status={status} />;
  }
  return <WaitingForEntry status={status} strategy={strategy} />;
}

/* ─────────────────────── ACTIVE POSITION ─────────────────────── */

function ActivePosition({ status }: { status: BotStatus }) {
  const pos = status.position!;
  // Use WS-streamed live price for real-time tick updates (REST is only polled every 5s).
  const wsPrice = useTickerStore(s => s.price);
  const live = wsPrice || status.live_price;
  const isLong = pos.side === 'LONG';
  const entry = pos.avg_entry;
  const tp = pos.tp_px ?? entry;
  const sl = pos.sl_px ?? entry;

  // L2 DCA trigger: only relevant if not yet filled (filled = 1)
  const showL2 = pos.filled < 2;
  const l2 = showL2 ? (isLong ? pos.worst_entry * (1 - 0.005) : pos.worst_entry * (1 + 0.005)) : null;

  // Unrealized — recompute from WS price every tick (don't trust status.fav_pct which is REST-stale)
  const sign = isLong ? 1 : -1;
  const unrealizedUsd = pos.qty_total * (live - entry) * sign;
  const unrealizedPct = ((live - entry) / entry) * 100 * sign;
  const isProfit = unrealizedPct >= 0;
  const notional = pos.qty_total * entry;

  return (
    <div class="card-elev p-0 overflow-hidden">
      {/* Top bar */}
      <div class="px-4 py-3 border-b border-bg-border flex items-start justify-between gap-3">
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2 flex-wrap">
            <span class={clsx('pill flex items-center gap-1', isLong ? 'pill-green' : 'pill-red')}>
              {isLong ? <TrendingUp size={11} strokeWidth={2.5}/> : <TrendingDown size={11} strokeWidth={2.5}/>}
              {pos.side}
            </span>
            <span class="text-sm font-mono text-text">
              {pos.qty_total.toFixed(4)} BTC <span class="text-text-muted">≈ ${notional.toFixed(2)}</span>
            </span>
          </div>
          <div class="mt-1 text-2xs text-text-dim">
            entry <span class="font-mono">${entry.toFixed(2)}</span>
            {pos.filled > 1 && <span class="ml-2 pill-orange">DCA L{pos.filled}</span>}
          </div>
        </div>
        <div class="text-right shrink-0">
          <div class={clsx('text-2xl md:text-3xl font-bold font-mono tabular-nums leading-none',
            isProfit ? 'text-accent-green' : 'text-accent-red')}>
            {isProfit ? '+' : ''}${unrealizedUsd.toFixed(2)}
          </div>
          <div class={clsx('text-sm font-mono mt-1',
            isProfit ? 'text-accent-green' : 'text-accent-red')}>
            {isProfit ? '+' : ''}{unrealizedPct.toFixed(2)}%
          </div>
        </div>
      </div>

      {/* Horizontal price scale */}
      <div class="p-4">
        <PriceScale isLong={isLong} entry={entry} live={live} tp={tp} sl={sl} l2={l2} />
      </div>

      {/* Opened timestamp */}
      {pos.entry_time && (
        <div class="px-4 pb-3 flex items-center gap-1.5 text-2xs text-text-dim">
          <Clock size={11} />
          opened {new Date(pos.entry_time).toLocaleString(undefined, {
            month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit'
          })}
        </div>
      )}
    </div>
  );
}

/* ─────────── PRICE SCALE (TP — Entry — L2 — SL with live mark) ─────────── */

function PriceScale({
  isLong, entry, live, tp, sl, l2,
}: {
  isLong: boolean;
  entry: number; live: number; tp: number; sl: number; l2: number | null;
}) {
  // Scale endpoints: low → high
  // For SHORT: TP (low, profit) ... SL (high, loss)
  // For LONG:  SL (low, loss)   ... TP (high, profit)
  const allKeys = [tp, entry, sl, ...(l2 ? [l2] : [])];
  const minPx = Math.min(...allKeys);
  const maxPx = Math.max(...allKeys);
  const range = Math.max(maxPx - minPx, 1);
  // 5% padding either side for visual breathing room
  const pad = range * 0.05;
  const scaleLo = minPx - pad;
  const scaleHi = maxPx + pad;
  const scaleSpan = scaleHi - scaleLo;
  const pctOf = (px: number) => Math.max(0, Math.min(100, ((px - scaleLo) / scaleSpan) * 100));

  const entryPct = pctOf(entry);
  const tpPct = pctOf(tp);
  const slPct = pctOf(sl);
  const l2Pct = l2 != null ? pctOf(l2) : null;
  const livePct = pctOf(live);

  const pctFromEntry = (px: number) => ((px - entry) / entry) * 100;
  const liveFromEntry = pctFromEntry(live);

  return (
    <div class="relative">
      {/* Top labels — TP / Entry / L2 / SL */}
      <div class="relative h-12 mb-2">
        <Marker pct={tpPct} color="green"  label="TP"    price={tp}    deltaPct={pctFromEntry(tp)} align="bottom" />
        <Marker pct={entryPct} color="white" label="ENTRY" price={entry} deltaPct={0} align="bottom" />
        {l2Pct != null && (
          <Marker pct={l2Pct} color="orange" label="L2"  price={l2!}   deltaPct={pctFromEntry(l2!)} align="bottom" />
        )}
        <Marker pct={slPct} color="red"   label="SL"    price={sl}    deltaPct={pctFromEntry(sl)} align="bottom" />
      </div>

      {/* The bar itself */}
      <div class="relative h-2 rounded-full overflow-visible">
        {/* Gradient track: green near TP, red near SL */}
        <div
          class="absolute inset-0 rounded-full opacity-60"
          style={{
            background: isLong
              ? 'linear-gradient(to right, rgba(246,70,93,0.4) 0%, rgba(43,49,57,0.3) 50%, rgba(14,203,129,0.5) 100%)'
              : 'linear-gradient(to right, rgba(14,203,129,0.5) 0%, rgba(43,49,57,0.3) 50%, rgba(246,70,93,0.4) 100%)',
          }}
        />
        {/* Tick marks for TP / ENTRY / L2 / SL */}
        <Tick pct={tpPct}    color="green"  />
        <Tick pct={entryPct} color="white"  />
        {l2Pct != null && <Tick pct={l2Pct} color="orange" />}
        <Tick pct={slPct}    color="red"    />
        {/* Current price marker */}
        <LiveMark pct={livePct} price={live} deltaPct={liveFromEntry} />
      </div>

      {/* Legend */}
      <div class="mt-6 flex items-center justify-between text-2xs">
        <span class="text-text-dim flex items-center gap-1">
          <span class="size-1.5 rounded-full bg-accent-green" />
          {isLong ? 'LOSS' : 'PROFIT'}
        </span>
        <span class="text-text-dim flex items-center gap-1">
          {isLong ? 'PROFIT' : 'LOSS'}
          <span class="size-1.5 rounded-full bg-accent-red" />
        </span>
      </div>
    </div>
  );
}

function Marker({ pct, color, label, price, deltaPct, align }: {
  pct: number; color: 'green' | 'red' | 'orange' | 'white'; label: string;
  price: number; deltaPct: number; align: 'top' | 'bottom';
}) {
  const colorCls = color === 'green' ? 'text-accent-green'
    : color === 'red' ? 'text-accent-red'
    : color === 'orange' ? 'text-accent-orange'
    : 'text-text';
  return (
    <div
      class="absolute top-0 -translate-x-1/2 text-center min-w-0"
      style={{ left: `${pct}%` }}
    >
      <div class={clsx('text-2xs font-semibold tracking-wide', colorCls)}>{label}</div>
      <div class={clsx('text-xs font-mono font-semibold', colorCls)}>
        ${price.toLocaleString(undefined, { maximumFractionDigits: 0 })}
      </div>
      <div class={clsx('text-2xs font-mono', deltaPct >= 0 ? 'text-accent-green' : 'text-accent-red')}>
        {deltaPct >= 0 ? '+' : ''}{deltaPct.toFixed(2)}%
      </div>
    </div>
  );
}

function Tick({ pct, color }: { pct: number; color: 'green' | 'red' | 'orange' | 'white' }) {
  const bgCls = color === 'green' ? 'bg-accent-green'
              : color === 'red' ? 'bg-accent-red'
              : color === 'orange' ? 'bg-accent-orange'
              : 'bg-text';
  return (
    <div
      class={clsx('absolute top-1/2 -translate-x-1/2 -translate-y-1/2 w-0.5 h-4 rounded-full', bgCls)}
      style={{ left: `${pct}%` }}
    />
  );
}

function LiveMark({ pct, price, deltaPct }: { pct: number; price: number; deltaPct: number }) {
  return (
    <div
      class="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 z-10"
      style={{ left: `${pct}%` }}
    >
      <div class="w-1 h-6 rounded-full bg-accent-blue ring-2 ring-bg-card shadow-glow" />
      <div class="absolute top-8 left-1/2 -translate-x-1/2 whitespace-nowrap bg-bg-card border border-accent-blue/40 rounded-md px-2 py-1 text-2xs font-mono">
        <span class="text-accent-blue mr-1">MARK</span>
        <span class="text-text font-semibold">${price.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
        <span class={clsx('ml-1', deltaPct >= 0 ? 'text-accent-green' : 'text-accent-red')}>
          {deltaPct >= 0 ? '+' : ''}{deltaPct.toFixed(2)}%
        </span>
      </div>
    </div>
  );
}

/* ─────────────────────── WAITING FOR ENTRY (no position) ─────────────────────── */

function WaitingForEntry({ status, strategy }: { status: BotStatus; strategy: StrategyId }) {
  const i = status.indicators;
  const rsi = i.rsi;
  const rsiOS = i.rsi_oversold ?? 30;
  const rsiOB = i.rsi_overbought ?? 70;
  const gap = i.trend_gap_pct;
  const gapMin = i.trend_gap_min_pct ?? 0.25;
  const trendUp = status.trend_15m === 'UP';
  const trendDown = status.trend_15m === 'DOWN';
  const isV2orV3 = strategy === 'rsiscalp_trend_v2' || strategy === 'rsiscalp_trend_v3';

  // Which side is the bot hunting? Determined by 15m trend.
  const huntingSide: 'LONG' | 'SHORT' | null =
    trendUp ? 'LONG' : trendDown ? 'SHORT' : null;

  type Cond = { label: string; value: string; ok: boolean };
  let conds: Cond[] = [];
  let nextCondHint = '';

  if (huntingSide === 'LONG') {
    conds = [
      { label: '15m trend UP',  value: status.trend_15m ?? '—', ok: trendUp },
      { label: `RSI ≤ ${rsiOS}`,
        value: `${rsi?.toFixed(1)}`,
        ok: rsi != null && rsi <= rsiOS },
    ];
    if (isV2orV3 && gap != null) {
      conds.push({
        label: `15m gap firm`,
        value: `${gap.toFixed(3)}% (need ≥ +${gapMin.toFixed(2)}%)`,
        ok: gap >= gapMin,
      });
    }
    nextCondHint = rsi != null
      ? `RSI needs to drop ${(rsi - rsiOS).toFixed(1)} to enter`
      : '';
  } else if (huntingSide === 'SHORT') {
    conds = [
      { label: '15m trend DOWN', value: status.trend_15m ?? '—', ok: trendDown },
      { label: `RSI ≥ ${rsiOB}`,
        value: `${rsi?.toFixed(1)}`,
        ok: rsi != null && rsi >= rsiOB },
    ];
    if (isV2orV3 && gap != null) {
      conds.push({
        label: `15m gap firm`,
        value: `${gap.toFixed(3)}% (need ≤ -${gapMin.toFixed(2)}%)`,
        ok: gap <= -gapMin,
      });
    }
    nextCondHint = rsi != null
      ? `RSI needs to rise ${(rsiOB - rsi).toFixed(1)} to enter`
      : '';
  }

  const metCount = conds.filter(c => c.ok).length;
  const block = status.block_reason;

  return (
    <div class="card-elev p-0 overflow-hidden">
      {/* Top bar */}
      <div class="px-4 py-3 border-b border-bg-border flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span class="section-title">Position</span>
          <span class="pill-muted">FLAT</span>
        </div>
        {huntingSide && (
          <span class={clsx('pill flex items-center gap-1',
            huntingSide === 'LONG' ? 'pill-green' : 'pill-red')}>
            {huntingSide === 'LONG' ? <TrendingUp size={11} strokeWidth={2.5}/> : <TrendingDown size={11} strokeWidth={2.5}/>}
            HUNTING {huntingSide}
          </span>
        )}
      </div>

      <div class="p-4">
        {/* Hero status */}
        <div class="mb-4">
          <div class="text-text-muted text-xs mb-1">Waiting for entry</div>
          <div class="text-xl font-semibold">
            {huntingSide
              ? <>Looking for <span class={huntingSide === 'LONG' ? 'text-accent-green' : 'text-accent-red'}>{huntingSide}</span> setup</>
              : <span class="text-text-muted">No clear trend</span>}
          </div>
          {nextCondHint && (
            <div class="text-2xs text-text-dim mt-1">{nextCondHint}</div>
          )}
        </div>

        {/* Conditions checklist */}
        {conds.length > 0 && (
          <div>
            <div class="flex items-center justify-between mb-2">
              <span class="text-2xs uppercase tracking-wider text-text-muted">Entry conditions</span>
              <span class="text-2xs font-mono text-text-muted">{metCount} / {conds.length} met</span>
            </div>
            <div class="space-y-2">
              {conds.map((c, i) => (
                <div key={i} class="flex items-center justify-between gap-3 text-sm py-1">
                  <div class="flex items-center gap-2 min-w-0">
                    <div class={clsx(
                      'size-4 rounded-full flex items-center justify-center shrink-0',
                      c.ok ? 'bg-accent-green/20 text-accent-green' : 'bg-bg-hover text-text-dim'
                    )}>
                      {c.ok ? <Check size={10} strokeWidth={3} /> : <X size={10} strokeWidth={3} />}
                    </div>
                    <span class={clsx(c.ok ? 'text-text' : 'text-text-muted')}>{c.label}</span>
                  </div>
                  <span class="text-xs font-mono text-text-dim truncate">{c.value}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Block reason */}
        {block && (
          <div class="mt-3 p-2.5 rounded-md bg-accent-orange/10 border border-accent-orange/30 text-2xs text-accent-orange leading-relaxed">
            <Activity size={11} class="inline mr-1" />
            {block}
          </div>
        )}
      </div>
    </div>
  );
}
