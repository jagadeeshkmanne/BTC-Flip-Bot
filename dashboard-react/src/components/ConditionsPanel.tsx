import { Check, X } from 'lucide-react';
import clsx from 'clsx';
import type { BotStatus, StrategyId } from '@/types/bot';

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
  // All bots except v1-legacy have GAP filter. v1 is now Ultimate (has GAP too).
  const hasGapFilter = ['rsiscalp_trend', 'rsiscalp_trend_v2',
                        'rsiscalp_trend_v4', 'rsiscalp_trend_v5'].includes(strategy);

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
    { label: '15m trend UP', value: s.trend_15m ?? '—', ok: trendUp },
  ];
  const SHORT: Cond[] = [
    { label: 'RSI ≥ overbought', value: `${rsi?.toFixed(1)} (need ≥ ${rsiOB})`, ok: rsi != null && rsi >= rsiOB },
    { label: '15m trend DOWN', value: s.trend_15m ?? '—', ok: trendDown },
  ];

  if (hasGapFilter && gap != null) {
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

  // Both sides — hour-filter check
  const hourCond: Cond = {
    label: 'Hour not blocked',
    value: hour != null ? `${String(hour).padStart(2, '0')}:00 UTC (blocked: ${blockedHours.map(h => String(h).padStart(2, '0')).join(',')})` : '—',
    ok: !inBlockedHour,
  };
  LONG.push(hourCond); SHORT.push(hourCond);

  // ATR chop filter
  if (atrPct != null) {
    const atrCond: Cond = {
      label: 'ATR not in chop',
      value: `${atrPct.toFixed(2)}% (need < ${atrMax.toFixed(2)}%)`,
      ok: atrPct <= atrMax,
    };
    LONG.push(atrCond); SHORT.push(atrCond);
  }

  // 1h cumulative move filter (direction-aware)
  if (chg1h != null) {
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
    <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
      <CondCard side="LONG" conds={LONG} />
      <CondCard side="SHORT" conds={SHORT} />
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
