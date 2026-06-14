import { Check, X } from 'lucide-react';
import clsx from 'clsx';
import type { BotStatus, StrategyId } from '@/types/bot';
import { utcHourToISTLabel } from '@/utils/time';

interface Cond { label: string; value: string; ok: boolean; }

function buildConditions(s: BotStatus, strategy: StrategyId): { LONG: Cond[]; SHORT: Cond[] } {
  const i = s.indicators as any;
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
  const isCounterTrend = strategy === 'v2.1' || strategy === 'v2.2'
    || (strategy === 'v2.3' && s.regime?.leg === 'range');

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

  return { LONG, SHORT };
}

export function ConditionsPanel({ status, strategy }: { status?: BotStatus; strategy: StrategyId }) {
  if (!status) return null;
  const { LONG, SHORT } = buildConditions(status, strategy);

  return (
    <div class="space-y-3">
      {status.regime && <RegimeBanner regime={status.regime} />}
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <CondCard side="LONG" conds={LONG} />
        <CondCard side="SHORT" conds={SHORT} />
      </div>
    </div>
  );
}

function RegimeBanner({ regime }: { regime: NonNullable<BotStatus['regime']> }) {
  const { leg, adx, trend_adx, range_adx, tf, range_on } = regime;
  const legLabel =
    leg === 'trend' ? 'TREND-FOLLOWING leg' :
    leg === 'range' ? 'COUNTER-TREND leg' :
                      'FLAT · dead zone (no new entries)';
  const legColor =
    leg === 'trend' ? 'bg-accent-green/15 text-accent-green' :
    leg === 'range' ? 'bg-accent-purple/15 text-accent-purple' :
                      'bg-accent-red/15 text-accent-red';
  const SCALE = 50;
  const clampPct = (v: number) => Math.max(0, Math.min(100, (v / SCALE) * 100));

  return (
    <div class="card">
      <div class="flex items-center justify-between mb-3">
        <span class="text-xs font-semibold uppercase tracking-wider text-text-muted">
          Regime · {tf} ADX(14)
        </span>
        <span class={clsx('text-2xs font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded', legColor)}>
          {legLabel}
        </span>
      </div>
      <div class="flex items-end gap-3 mb-3">
        <div class="text-3xl font-bold font-mono leading-none">
          {adx != null ? adx.toFixed(1) : '—'}
        </div>
        <div class="text-2xs text-text-dim leading-relaxed pb-0.5">
          range &lt; {range_adx}{range_on ? '' : ' (off)'} · dead {range_adx}–{trend_adx} · trend ≥ {trend_adx}
        </div>
      </div>
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

function CondCard({ side, conds }: { side: 'LONG' | 'SHORT'; conds: Cond[] }) {
  const met = conds.filter(c => c.ok).length;
  const isLong = side === 'LONG';
  return (
    <div class="card">
      <div class="flex items-center justify-between mb-3">
        <span class={clsx('pill', isLong ? 'pill-green' : 'pill-red')}>{side}</span>
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
