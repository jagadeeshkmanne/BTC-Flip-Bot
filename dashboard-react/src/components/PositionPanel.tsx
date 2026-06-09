import { useEffect, useState } from 'preact/hooks';
import clsx from 'clsx';
import { TrendingDown, TrendingUp, Check, X, Clock, Activity } from 'lucide-react';
import type { BotStatus, BotState, StrategyId } from '@/types/bot';
import { useTickerStore } from '@/hooks/useBtcStream';

// Compute "time until bot resumes" given the blocked hours list (UTC).
// Returns null if not currently blocked. Otherwise returns ms until the
// next minute after the last consecutive blocked hour ends.
function msUntilResume(blockedHours: number[]): number | null {
  if (!blockedHours.length) return null;
  const now = new Date();
  const curHour = now.getUTCHours();
  if (!blockedHours.includes(curHour)) return null;
  // Find the next hour NOT in blockedHours (handles non-contiguous lists too).
  let h = curHour;
  for (let i = 0; i < 24; i++) {
    h = (h + 1) % 24;
    if (!blockedHours.includes(h)) break;
  }
  // Next target: H:00:00 UTC. Compute date for that hour.
  const target = new Date(now);
  target.setUTCMinutes(0, 0, 0);
  // Bump hours forward until we reach the unblocked hour
  let advance = (h - curHour + 24) % 24;
  if (advance === 0) advance = 24;
  target.setUTCHours(curHour + advance);
  return target.getTime() - now.getTime();
}

function fmtCountdown(ms: number): string {
  if (ms <= 0) return '0m 0s';
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  return `${m}m ${s.toString().padStart(2, '0')}s`;
}

interface Props {
  status?: BotStatus;
  state?: BotState;
  strategy: StrategyId;
}

export function PositionPanel({ status, state, strategy }: Props) {
  if (!status) {
    return <div class="card-elev py-12 text-center text-text-muted text-sm">Loading…</div>;
  }
  if (status.position) {
    return <ActivePosition status={status} />;
  }
  return <WaitingForEntry status={status} state={state} strategy={strategy} />;
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
        <div class="text-right shrink-0 min-w-0">
          <div class={clsx('text-lg sm:text-xl md:text-3xl font-bold font-mono tabular-nums leading-none truncate',
            isProfit ? 'text-accent-green' : 'text-accent-red')}>
            {isProfit ? '+' : ''}${unrealizedUsd.toFixed(2)}
          </div>
          <div class={clsx('text-2xs md:text-sm font-mono mt-1',
            isProfit ? 'text-accent-green' : 'text-accent-red')}>
            {isProfit ? '+' : ''}{unrealizedPct.toFixed(2)}%
          </div>
        </div>
      </div>

      {/* Horizontal price scale */}
      <div class="p-4">
        <PriceScale isLong={isLong} entry={entry} live={live} tp={tp} sl={sl} l2={l2} />
      </div>

      {/* Opened timestamp + v1.1 Time-SL countdown */}
      <div class="px-4 pb-3 flex items-center justify-between gap-3 text-2xs text-text-dim flex-wrap">
        {pos.entry_time && (
          <div class="flex items-center gap-1.5">
            <Clock size={11} />
            opened {new Date(pos.entry_time).toLocaleString(undefined, {
              month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit'
            })}
          </div>
        )}
        {(pos as any).time_sl_at && (() => {
          const target = new Date((pos as any).time_sl_at).getTime();
          const now = Date.now();
          const msLeft = Math.max(0, target - now);
          const totalMin = Math.floor(msLeft / 60_000);
          const hours = Math.floor(totalMin / 60);
          const mins = totalMin % 60;
          const countdown = hours > 0 ? `${hours}h ${mins}m left` : `${mins}m left`;
          const targetTime = new Date(target).toLocaleTimeString(undefined, {
            hour: '2-digit', minute: '2-digit'
          });
          return (
            <div class="flex items-center gap-1.5 text-accent-orange font-mono">
              <Clock size={11} />
              <span class="uppercase tracking-wider">Time-SL</span>
              <span>{countdown}</span>
              <span class="text-text-dim">(at {targetTime})</span>
            </div>
          );
        })()}
      </div>
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

function WaitingForEntry({ status, state, strategy }: { status: BotStatus; state?: BotState; strategy: StrategyId }) {
  const i = status.indicators;
  const rsi = i.rsi;
  const rsiOS = i.rsi_oversold ?? 30;
  const rsiOB = i.rsi_overbought ?? 70;
  const gap = i.trend_gap_pct;
  const gapMin = i.trend_gap_min_pct ?? 0.25;
  const trendUp = status.trend_15m === 'UP';
  const trendDown = status.trend_15m === 'DOWN';
  // v3 = counter-trend bot (UI label "v2"). Bypasses the 15m trend gate.
  const isCounterTrend = strategy === 'rsiscalp_trend_v3';

  // Which side is the bot hunting?
  // - v1 (with-trend): determined by 15m trend direction
  // - v2 (counter-trend): hunting BOTH sides until RSI hits an extreme
  let huntingSide: 'LONG' | 'SHORT' | null;
  if (isCounterTrend) {
    // Hunt whichever side is closer to its RSI threshold
    if (rsi == null) huntingSide = null;
    else if (rsi <= 50) huntingSide = 'LONG';
    else huntingSide = 'SHORT';
  } else {
    huntingSide = trendUp ? 'LONG' : trendDown ? 'SHORT' : null;
  }

  type Cond = { label: string; value: string; ok: boolean };
  let conds: Cond[] = [];
  let nextCondHint = '';

  if (huntingSide === 'LONG') {
    conds = [
      { label: `RSI ≤ ${rsiOS}`,
        value: `${rsi?.toFixed(1)}`,
        ok: rsi != null && rsi <= rsiOS },
    ];
    // v1 requires trend match; v2 doesn't
    if (!isCounterTrend) {
      conds.push({ label: '15m trend UP', value: status.trend_15m ?? '—', ok: trendUp });
    }
    if (gap != null) {
      if (isCounterTrend) {
        conds.push({
          label: '15m gap firm',
          value: `|${gap.toFixed(3)}%| (need ≥ ${gapMin.toFixed(2)}%)`,
          ok: Math.abs(gap) >= gapMin,
        });
      } else {
        conds.push({
          label: '15m gap firm',
          value: `${gap.toFixed(3)}% (need ≥ +${gapMin.toFixed(2)}%)`,
          ok: gap >= gapMin,
        });
      }
    }
    nextCondHint = rsi != null
      ? `RSI needs to drop ${(rsi - rsiOS).toFixed(1)} to enter`
      : '';
  } else if (huntingSide === 'SHORT') {
    conds = [
      { label: `RSI ≥ ${rsiOB}`,
        value: `${rsi?.toFixed(1)}`,
        ok: rsi != null && rsi >= rsiOB },
    ];
    if (!isCounterTrend) {
      conds.push({ label: '15m trend DOWN', value: status.trend_15m ?? '—', ok: trendDown });
    }
    if (gap != null) {
      if (isCounterTrend) {
        conds.push({
          label: '15m gap firm',
          value: `|${gap.toFixed(3)}%| (need ≥ ${gapMin.toFixed(2)}%)`,
          ok: Math.abs(gap) >= gapMin,
        });
      } else {
        conds.push({
          label: '15m gap firm',
          value: `${gap.toFixed(3)}% (need ≤ -${gapMin.toFixed(2)}%)`,
          ok: gap <= -gapMin,
        });
      }
    }
    nextCondHint = rsi != null
      ? `RSI needs to rise ${(rsiOB - rsi).toFixed(1)} to enter`
      : '';
  }

  const metCount = conds.filter(c => c.ok).length;
  const block = status.block_reason;
  // 2026-06-05: hour-blocked state for prominent "WAITING" display
  const blockedHours: number[] = (status as any).blocked_hours || (status.indicators as any).blocked_hours || [];
  const curHour: number | null = (status as any).current_hour_utc ?? (status.indicators as any).current_hour_utc ?? null;
  const hourBlocked = curHour != null && blockedHours.includes(curHour);

  return (
    <div class="card-elev p-0 overflow-hidden">
      {/* Top bar — unmistakable FLAT state */}
      <div class="px-4 py-3 border-b border-bg-border bg-bg-subtle flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span class="section-title">Position</span>
          <span class="pill flex items-center gap-1 bg-bg-hover text-text-muted border border-bg-border">
            <span class="size-1.5 rounded-full bg-text-dim" />
            NO TRADE
          </span>
        </div>
        {huntingSide && (
          <div class="flex items-center gap-1.5 text-2xs text-text-muted">
            <span>watching for</span>
            <span class={clsx('font-semibold tracking-wide',
              huntingSide === 'LONG' ? 'text-accent-green' : 'text-accent-red')}>
              {huntingSide}
            </span>
            {huntingSide === 'LONG'
              ? <TrendingUp size={12} strokeWidth={2.5} class={huntingSide === 'LONG' ? 'text-accent-green' : 'text-accent-red'} />
              : <TrendingDown size={12} strokeWidth={2.5} class="text-accent-red" />}
          </div>
        )}
      </div>

      <div class="p-4 space-y-4">
        {/* Big unambiguous empty state */}
        <div class="text-center py-2">
          <div class="text-text-muted text-sm mb-1">No active position</div>
          <div class="text-text-dim text-2xs">
            Bot is monitoring · last tick {(status.updated_at || '').slice(11, 19)} UTC
          </div>
        </div>

        {/* Hour-blocked banner with LIVE countdown to resume */}
        {hourBlocked && (
          <HourBlockedBanner blockedHours={blockedHours} curHour={curHour ?? 0} />
        )}

        {/* Post-loss cooldown banner */}
        {state?.pause_until && <CooldownBanner pauseUntil={state.pause_until} />}

        {/* Daily-loss stop banner */}
        {state?.daily_loss != null && state.daily_loss <= -200 && (
          <div class="rounded-lg border border-accent-red/30 bg-accent-red/5 p-3 text-sm">
            <div class="flex items-center gap-2">
              <X size={16} class="text-accent-red" />
              <span class="font-semibold text-accent-red">Daily $200 stop hit</span>
            </div>
            <div class="text-xs text-text-muted mt-1 ml-6">
              Today's loss: ${state.daily_loss.toFixed(2)} · resumes 00:00 UTC
            </div>
          </div>
        )}

        {/* RSI gauge — clearly labeled as "RSI Indicator", not a position scale */}
        {rsi != null && huntingSide && (
          <div class="border-t border-bg-border pt-4">
            <RsiGauge rsi={rsi} target={huntingSide === 'LONG' ? rsiOS : rsiOB}
                      rsiOS={rsiOS} rsiOB={rsiOB} hunting={huntingSide} />
          </div>
        )}

        {/* Conditions checklist — bigger values, colored by status */}
        {conds.length > 0 && (
          <div>
            <div class="flex items-center justify-between mb-2">
              <span class="text-2xs uppercase tracking-wider text-text-muted">Entry conditions</span>
              <span class="text-2xs font-mono text-text-muted">{metCount} / {conds.length} met</span>
            </div>
            <div class="space-y-2.5">
              {conds.map((c, i) => (
                <div key={i} class="flex items-center justify-between gap-3 py-1">
                  <div class="flex items-center gap-2 min-w-0">
                    <div class={clsx(
                      'size-5 rounded-full flex items-center justify-center shrink-0',
                      c.ok ? 'bg-accent-green/20 text-accent-green' : 'bg-bg-hover text-text-dim'
                    )}>
                      {c.ok ? <Check size={12} strokeWidth={3} /> : <X size={12} strokeWidth={3} />}
                    </div>
                    <span class={clsx('text-sm', c.ok ? 'text-text' : 'text-text-muted')}>{c.label}</span>
                  </div>
                  <span class={clsx(
                    'text-sm font-mono font-semibold tabular-nums shrink-0',
                    c.ok ? 'text-accent-green' : 'text-text'
                  )}>{c.value}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Block reason */}
        {block && (
          <div class="p-2.5 rounded-md bg-accent-orange/10 border border-accent-orange/30 text-2xs text-accent-orange leading-relaxed">
            <Activity size={11} class="inline mr-1" />
            {block}
          </div>
        )}
      </div>
    </div>
  );
}

/* RSI gauge — visual scale from 0-100 with marker at current RSI + target threshold. */
function RsiGauge({ rsi, target, rsiOS, rsiOB, hunting }:
  { rsi: number; target: number; rsiOS: number; rsiOB: number; hunting: 'LONG' | 'SHORT' }) {
  const isLong = hunting === 'LONG';
  // For LONG: need RSI ≤ rsiOS. For SHORT: need RSI ≥ rsiOB.
  const distance = isLong ? rsi - target : target - rsi;     // > 0 means not there yet
  const isMet = distance <= 0;
  const colorCls = isMet ? 'text-accent-green' : isLong ? 'text-accent-blue' : 'text-accent-orange';

  return (
    <div>
      <div class="flex items-end justify-between mb-2">
        <div>
          <div class="text-2xs uppercase tracking-wider text-text-muted">RSI 9</div>
          <div class={clsx('text-3xl font-bold font-mono tabular-nums leading-none', colorCls)}>
            {rsi.toFixed(1)}
          </div>
        </div>
        <div class="text-right">
          <div class="text-2xs text-text-dim">target</div>
          <div class={clsx('text-base font-mono font-semibold',
            isLong ? 'text-accent-green' : 'text-accent-red')}>
            {isLong ? '≤' : '≥'} {target}
          </div>
          <div class="text-2xs text-text-muted mt-0.5">
            {isMet
              ? <span class="text-accent-green">✓ ready</span>
              : <span>needs {distance.toFixed(1)} more {isLong ? '↓' : '↑'}</span>}
          </div>
        </div>
      </div>
      {/* 0-100 bar with markers — thresholds now driven by ACTUAL bot config (rsiOS/rsiOB) */}
      <div class="relative h-2 rounded-full bg-bg-subtle overflow-visible">
        {/* Oversold zone (0-rsiOS) and overbought zone (rsiOB-100) tinted */}
        <div class="absolute inset-y-0 left-0 bg-accent-green/15 rounded-l-full"
             style={{ width: `${rsiOS}%` }} />
        <div class="absolute inset-y-0 right-0 bg-accent-red/15 rounded-r-full"
             style={{ width: `${100 - rsiOB}%` }} />
        {/* Threshold markers (use real values, not hardcoded 30/70) */}
        <div class="absolute top-0 bottom-0 w-px bg-accent-green/60" style={{ left: `${rsiOS}%` }} />
        <div class="absolute top-0 bottom-0 w-px bg-accent-red/60" style={{ left: `${rsiOB}%` }} />
        {/* Current RSI marker */}
        <div
          class="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 z-10"
          style={{ left: `${Math.max(0, Math.min(100, rsi))}%` }}
        >
          <div class={clsx('w-1 h-5 rounded-full ring-2 ring-bg-card',
            isMet ? 'bg-accent-green' : isLong ? 'bg-accent-blue' : 'bg-accent-orange')} />
        </div>
      </div>
      <div class="relative h-4 mt-1 text-2xs text-text-dim">
        <span class="absolute left-0">0</span>
        <span class="absolute text-accent-green -translate-x-1/2" style={{ left: `${rsiOS}%` }}>{rsiOS}</span>
        <span class="absolute -translate-x-1/2" style={{ left: '50%' }}>50</span>
        <span class="absolute text-accent-red -translate-x-1/2" style={{ left: `${rsiOB}%` }}>{rsiOB}</span>
        <span class="absolute right-0">100</span>
      </div>
    </div>
  );
}

/* Post-loss cooldown banner with live countdown. */
function CooldownBanner({ pauseUntil }: { pauseUntil: string }) {
  const targetMs = new Date(pauseUntil).getTime();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const msLeft = targetMs - now;
  if (msLeft <= 0) return null;  // expired

  const totalSec = Math.ceil(msLeft / 1000);
  const mins = Math.floor(totalSec / 60);
  const secs = totalSec % 60;
  const countdown = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;

  return (
    <div class="rounded-lg border border-accent-orange/30 bg-accent-orange/5 p-3 text-sm">
      <div class="flex items-center gap-2">
        <Clock size={16} class="text-accent-orange" />
        <span class="font-semibold text-accent-orange">Cooldown after loss</span>
        <span class="ml-auto font-mono text-accent-orange">{countdown} left</span>
      </div>
      <div class="text-xs text-text-muted mt-1 ml-6">
        Resumes at {new Date(targetMs).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
      </div>
    </div>
  );
}

/* Live countdown timer for the hour-blocked banner. Ticks every second. */
function HourBlockedBanner({ blockedHours, curHour }:
  { blockedHours: number[]; curHour: number }) {
  const [remaining, setRemaining] = useState<number>(() => msUntilResume(blockedHours) ?? 0);

  useEffect(() => {
    const t = setInterval(() => {
      const ms = msUntilResume(blockedHours);
      setRemaining(ms ?? 0);
    }, 1000);
    return () => clearInterval(t);
  }, [blockedHours.join(',')]);

  // After last contiguous blocked hour ends — that's resume time
  let nextOpen = curHour;
  for (let i = 0; i < 24; i++) {
    nextOpen = (nextOpen + 1) % 24;
    if (!blockedHours.includes(nextOpen)) break;
  }
  const resumeAtUtc = `${nextOpen.toString().padStart(2,'0')}:00 UTC`;

  return (
    <div class="p-3 rounded-md bg-accent-orange/15 border border-accent-orange/40 flex items-center gap-3">
      <Activity size={18} class="text-accent-orange shrink-0 animate-pulse-slow" />
      <div class="flex-1 min-w-0">
        <div class="flex items-baseline justify-between gap-3 flex-wrap">
          <span class="text-sm font-semibold text-accent-orange">
            Waiting — high-risk hour blocked
          </span>
          <span class="text-base font-bold font-mono text-accent-orange tabular-nums">
            {fmtCountdown(remaining)}
          </span>
        </div>
        <div class="text-2xs text-text-muted mt-1">
          {curHour.toString().padStart(2,'0')}:00 UTC is in blocked window
          ({blockedHours.map(h => `${h.toString().padStart(2,'0')}:00`).join(', ')})
          {' · '}
          resumes at <span class="text-text font-semibold">{resumeAtUtc}</span>
        </div>
      </div>
    </div>
  );
}
