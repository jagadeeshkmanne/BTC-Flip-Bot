import { Check, X } from 'lucide-react';
import clsx from 'clsx';
import type { BotStatus, StrategyId } from '@/types/bot';
import { utcHourToISTLabel } from '@/utils/time';

interface Cond { label: string; value: string; ok: boolean; }

function buildConditions(s: BotStatus, strategy: StrategyId): { LONG: Cond[]; SHORT: Cond[] } {
  const i = s.indicators as any;

  // ── trend_btc: dynamic-leverage 4h trend (no RSI / no shorts) ──
  if (strategy === 'trend_btc') {
    const px = s.live_price ?? s.price;
    const LONG: Cond[] = [
      { label: 'EMA13 > EMA20 (fast above slow)', value: `${i.ema_f?.toFixed(0)} > ${i.ema_s?.toFixed(0)}`, ok: i.ema_f > i.ema_s },
      { label: 'Price > EMA200 (uptrend)', value: `${px?.toFixed(0)} vs ${i.ema_g?.toFixed(0)}`, ok: px > i.ema_g },
    ];
    const conviction: Cond[] = [
      { label: 'ADX > 20 (trend strength → 5× cap)', value: `${i.adx?.toFixed(0)}`, ok: i.adx > 20 },
      { label: 'Weekly close > weekly EMA50', value: i.weekly_bull ? 'bull' : 'bear', ok: !!i.weekly_bull },
      { label: 'Daily not in bear (price > daily EMA200)', value: i.daily_bear ? 'bear → ½ size' : 'ok', ok: !i.daily_bear },
      { label: 'Effective leverage', value: `${i.leverage?.toFixed(2)}× (cap ${i.cap}×)`, ok: true },
    ];
    return { LONG, SHORT: conviction };
  }

  // ── btcv2: macro-filtered MTF long + bear-depth short (4h). No RSI/ADX/15m. ──
  if (strategy === 'btcv2') {
    const g = (s as any).v2gates ?? {};
    const ddh = g.dd_from_high != null ? (g.dd_from_high * 100).toFixed(0) : '—';
    const LONG: Cond[] = [
      { label: '4h trend up (EMA50 > EMA200)', value: `${i.ema50?.toFixed(0)} vs ${i.ema200?.toFixed(0)}`, ok: !!g.f_bull },
      { label: 'Daily trend up (EMA50 > EMA200)', value: g.d_bull ? 'bull' : 'bear', ok: !!g.d_bull },
      { label: 'Macro: price > 9-month SMA', value: g.macro_ok ? 'above' : 'below', ok: !!g.macro_ok },
    ];
    const SHORT: Cond[] = [
      { label: 'Down > 10% from 40-day high', value: g.drop_ok ? `yes (${ddh}% off high)` : 'no', ok: !!g.drop_ok },
      { label: 'Daily MACD < signal (bear)', value: g.d_macd_bear ? 'bear' : 'bull', ok: !!g.d_macd_bear },
      { label: 'Bear-depth short size', value: `${g.ssize ?? '—'}× @ ${ddh}% drawdown`, ok: true },
    ];
    return { LONG, SHORT };
  }

  const rsi = i.rsi;
  const rsiOS = i.rsi_oversold ?? 30;
  const rsiOB = i.rsi_overbought ?? 70;
  const gap = i.trend_gap_pct;
  const gapMin = i.trend_gap_min_pct ?? 0.25;

  const trendUp = s.trend_15m === 'UP';
  const trendDown = s.trend_15m === 'DOWN';
  // Both active bots (v11/v3) have GAP filter.
  const hasGapFilter = true;
  // 2026-06-10: v2.1 + v2.2 are both COUNTER-TREND.
  // 2026-06-14: v2.3 regime router switches leg by 1h ADX — counter-trend only
  // when the active leg is 'range'; with-trend when 'trend'.
  const isCounterTrend = strategy === 'v2.3' && s.regime?.leg === 'range';

  // Fleet-wide filters
  const hour = i.current_hour_utc;
  const blockedHours: number[] = i.blocked_hours ?? [];
  const inBlockedHour = hour != null && blockedHours.includes(hour);
  const atrPct = i.atr_pct;
  const atrMax = i.atr_max_pct ?? 0.60;
  const chg1h = i.chg_1h_pct;
  const chg1hMax = i.chg_1h_max_pct ?? 2.0;

  const LONG: Cond[] = [
    { label: 'RSI ≤ oversold', value: `${rsi?.toFixed(1)} (need ≤ ${rsiOS})`, ok: rsi != null && rsi <= rsiOS },
  ];
  const SHORT: Cond[] = [
    { label: 'RSI ≥ overbought', value: `${rsi?.toFixed(1)} (need ≥ ${rsiOB})`, ok: rsi != null && rsi >= rsiOB },
  ];

  // Only with-trend bot (v1) requires 15m trend direction match.
  if (!isCounterTrend) {
    LONG.push({ label: '15m trend UP', value: s.trend_15m ?? '—', ok: trendUp });
    SHORT.push({ label: '15m trend DOWN', value: s.trend_15m ?? '—', ok: trendDown });
  }

  if (hasGapFilter && gap != null) {
    // Counter-trend (v2) only requires |gap| ≥ threshold, sign-agnostic.
    if (isCounterTrend) {
      const gapCond: Cond = {
        label: '15m gap firm',
        value: `|${gap.toFixed(3)}%| (need ≥ ${gapMin.toFixed(2)}%)`,
        ok: Math.abs(gap) >= gapMin,
      };
      LONG.push(gapCond);
      SHORT.push(gapCond);
    } else {
      LONG.push({
        label: '15m gap firm (UP)',
        value: `${gap.toFixed(3)}% (need ≥ +${gapMin.toFixed(2)}%)`,
        ok: gap >= gapMin,
      });
      SHORT.push({
        label: '15m gap firm (DOWN)',
        value: `${gap.toFixed(3)}% (need ≤ -${gapMin.toFixed(2)}%)`,
        ok: gap <= -gapMin,
      });
    }
  }

  // Hour-filter — only show if bot has blocked hours configured
  if (blockedHours.length > 0) {
    const hourCond: Cond = {
      label: 'Hour not blocked',
      value: hour != null ? `${String(hour).padStart(2, '0')}:00 UTC (blocked: ${blockedHours.map(h => String(h).padStart(2, '0')).join(',')})` : '—',
      ok: !inBlockedHour,
    };
    LONG.push(hourCond); SHORT.push(hourCond);
  }

  // ATR chop filter — only show if bot has a meaningful threshold (<10%)
  if (atrPct != null && atrMax < 10) {
    const atrCond: Cond = {
      label: 'ATR not in chop',
      value: `${atrPct.toFixed(2)}% (need < ${atrMax.toFixed(2)}%)`,
      ok: atrPct <= atrMax,
    };
    LONG.push(atrCond); SHORT.push(atrCond);
  }

  // 1h cumulative move filter — only show if bot has meaningful threshold (<10%)
  if (chg1h != null && chg1hMax < 10) {
    LONG.push({
      label: '1h move not collapsing',
      value: `${chg1h >= 0 ? '+' : ''}${chg1h.toFixed(2)}% (need > -${chg1hMax.toFixed(1)}%)`,
      ok: chg1h > -chg1hMax,
    });
    SHORT.push({
      label: '1h move not rallying',
      value: `${chg1h >= 0 ? '+' : ''}${chg1h.toFixed(2)}% (need < +${chg1hMax.toFixed(1)}%)`,
      ok: chg1h < chg1hMax,
    });
  }

  // 2026-06-14: v2.3 — 1h regime as a top-level condition (like the 15m gap row).
  // The regime is the MASTER gate: it decides which leg is active and whether an
  // entry is allowed at all. Shown first so it reads top-down.
  if (s.regime) {
    const r = s.regime;
    const adxTxt = r.adx != null ? r.adx.toFixed(1) : '—';
    const stateLabel =
      r.leg === 'range' ? 'RANGE' :
      r.leg === 'trend' ? (r.dir === 'up' ? 'BULL ↑' : 'BEAR ↓') :
                          `FLAT (dead ${r.range_adx}–${r.trend_adx})`;
    // LONG permitted when: range leg (fade both ways) OR trend leg in an uptrend.
    const longOk = r.leg === 'range' || (r.leg === 'trend' && r.dir === 'up');
    const shortOk = r.leg === 'range' || (r.leg === 'trend' && r.dir === 'down');
    LONG.unshift({ label: '1h regime allows LONG', value: `${stateLabel} · ADX ${adxTxt}`, ok: longOk });
    SHORT.unshift({ label: '1h regime allows SHORT', value: `${stateLabel} · ADX ${adxTxt}`, ok: shortOk });
  }

  return { LONG, SHORT };
}

export function ConditionsPanel({ status, strategy }: { status?: BotStatus; strategy: StrategyId }) {
  if (!status) return null;
  const { LONG, SHORT } = buildConditions(status, strategy);

  return (
    <div class="space-y-3">
      {status.regime && <RegimeBanner regime={status.regime} />}
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        {strategy === 'trend_btc' ? (
          <>
            <CondCard side="LONG" title="ENTRY (long)" conds={LONG} />
            <CondCard side="LONG" title="CONVICTION → LEVERAGE" conds={SHORT} />
          </>
        ) : (
          <>
            <CondCard side="LONG" conds={LONG} />
            <CondCard side="SHORT" conds={SHORT} />
          </>
        )}
      </div>
    </div>
  );
}

function RegimeBanner({ regime }: { regime: NonNullable<BotStatus['regime']> }) {
  const { leg, adx, dir, trend_adx, range_adx, tf, range_on } = regime;
  const up = dir === 'up';

  // Market STATE = strength (leg) + direction (dir)
  const state =
    leg === 'range' ? { label: 'RANGE', arrow: '↔', color: 'bg-accent-purple/15 text-accent-purple',
                        sub: 'sideways · counter-trend leg active (fade extremes)' } :
    leg === 'trend' ? (up
      ? { label: 'BULL', arrow: '↑', color: 'bg-accent-green/15 text-accent-green',
          sub: 'uptrend · trend leg active (long pullbacks)' }
      : { label: 'BEAR', arrow: '↓', color: 'bg-accent-red/15 text-accent-red',
          sub: 'downtrend · trend leg active (short rallies)' })
    : { label: `TRANSITION ${up ? '↑' : '↓'}`, arrow: '', color: 'bg-accent-orange/15 text-accent-orange',
        sub: `dead zone (ADX ${range_adx}–${trend_adx}) · no new entries, leaning ${up ? 'up' : 'down'}` };

  const SCALE = 50;
  const clampPct = (v: number) => Math.max(0, Math.min(100, (v / SCALE) * 100));

  return (
    <div class="card">
      <div class="flex items-center justify-between mb-3">
        <span class="text-xs font-semibold uppercase tracking-wider text-text-muted">
          Regime · {tf} ADX(14)
        </span>
        <span class={clsx('text-xs font-bold uppercase tracking-wider px-2 py-0.5 rounded', state.color)}>
          {state.arrow} {state.label}
        </span>
      </div>
      <div class="flex items-end gap-3 mb-1">
        <div class="text-3xl font-bold font-mono leading-none">
          {adx != null ? adx.toFixed(1) : '—'}
        </div>
        <div class="text-2xs text-text-dim leading-relaxed pb-0.5">
          ADX = strength · {up ? '+DI > −DI (up)' : '−DI > +DI (down)'}<br />
          range &lt; {range_adx}{range_on ? '' : ' (off)'} · dead {range_adx}–{trend_adx} · trend ≥ {trend_adx}
        </div>
      </div>
      <div class="text-2xs text-text-muted mb-2">{state.sub}</div>
      {/* gauge: range zone (purple) | dead zone | trend zone (green), marker at live ADX */}
      <div class="relative h-2 rounded-full overflow-hidden bg-bg-hover">
        <div class="absolute inset-y-0 left-0 bg-accent-purple/40" style={{ width: `${clampPct(range_adx)}%` }} />
        <div class="absolute inset-y-0 bg-accent-green/40" style={{ left: `${clampPct(trend_adx)}%`, right: '0' }} />
        {adx != null && (
          <div
            class="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 size-3 rounded-full bg-text shadow"
            style={{ left: `${clampPct(adx)}%` }}
          />
        )}
      </div>
      <div class="flex justify-between text-2xs text-text-dim mt-1 font-mono">
        <span>0</span><span>{range_adx}</span><span>{trend_adx}</span><span>{SCALE}+</span>
      </div>
    </div>
  );
}

function CondCard({ side, conds, title }: { side: 'LONG' | 'SHORT'; conds: Cond[]; title?: string }) {
  const met = conds.filter(c => c.ok).length;
  const isLong = side === 'LONG';
  return (
    <div class="card">
      <div class="flex items-center justify-between mb-3">
        <span class={clsx('pill', isLong ? 'pill-green' : 'pill-red')}>{title ?? side}</span>
        <span class="text-xs text-text-muted">{met}/{conds.length} met</span>
      </div>
      <div class="space-y-2">
        {conds.map((c, i) => (
          <div key={i} class="flex items-start gap-2 text-sm">
            <div class={clsx(
              'mt-0.5 size-4 rounded-full flex items-center justify-center shrink-0',
              c.ok ? 'bg-accent-green/20 text-accent-green' : 'bg-bg-hover text-text-dim'
            )}>
              {c.ok ? <Check size={10} strokeWidth={3} /> : <X size={10} strokeWidth={3} />}
            </div>
            <div class="min-w-0 flex-1">
              <div class={c.ok ? 'text-text' : 'text-text-muted'}>{c.label}</div>
              <div class="text-xs text-text-dim font-mono">{c.value}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
