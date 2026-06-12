import { Check, X } from 'lucide-react';
import clsx from 'clsx';

// v3 4h trend portfolio — per-pair entry criteria (required vs met).
// Reads the v3 bot's status.indicators: { PAIR: { close, ema_fast, ema_slow,
// ema_exit, adx } } and status.signals: { PAIR: 'LONG' | 'FLAT' }.

interface Cond { label: string; value: string; ok: boolean; }

const fmt = (x: number | null | undefined, digits = 0) =>
  x == null || Number.isNaN(x) ? '—' : x.toLocaleString(undefined, { maximumFractionDigits: digits });

export function V3ConditionsPanel({ status }: { status?: any }) {
  if (!status?.indicators) return null;
  const ind = status.indicators as Record<string, any>;
  const sigs = (status.signals ?? {}) as Record<string, string>;
  const pairs = Object.keys(ind);
  if (!pairs.length || ind[pairs[0]]?.ema_fast === undefined) return null;
  const btcLong = sigs['BTCUSDT'] === 'LONG';

  return (
    <div>
      <div class="flex items-center justify-between mb-2">
        <span class="text-sm font-medium text-text">Entry criteria per pair <span class="text-text-dim">(closed 4h bar)</span></span>
        <span class="text-xs text-text-muted">LONG needs all · else FLAT</span>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        {pairs.map(p => {
          const x = ind[p];
          const isBtc = p === 'BTCUSDT';
          const conds: Cond[] = [
            { label: 'Trend up: EMA30 > EMA150',
              value: `${fmt(x.ema_fast)} vs ${fmt(x.ema_slow)}`,
              ok: x.ema_fast != null && x.ema_slow != null && x.ema_fast > x.ema_slow },
            { label: 'Price above exit line: close > EMA50',
              value: `${fmt(x.close)} vs ${fmt(x.ema_exit)}`,
              ok: x.close != null && x.ema_exit != null && x.close > x.ema_exit },
            { label: 'Trend strength: ADX(14) > 20',
              value: `${x.adx != null ? x.adx.toFixed(1) : '—'} (need > 20)`,
              ok: x.adx != null && x.adx > 20 },
          ];
          if (!isBtc) {
            conds.push({ label: 'Leader gate: BTC in uptrend',
              value: btcLong ? 'BTC LONG' : 'BTC flat',
              ok: btcLong });
          }
          const met = conds.filter(c => c.ok).length;
          const long = sigs[p] === 'LONG';
          return (
            <div key={p} class="card">
              <div class="flex items-center justify-between mb-3">
                <span class="text-sm font-semibold text-text">{p.replace('USDT', '')}</span>
                <div class="flex items-center gap-2">
                  <span class={clsx('pill', long ? 'pill-green' : 'pill-orange')}>{long ? 'LONG' : 'FLAT'}</span>
                  <span class="text-xs text-text-muted">{met}/{conds.length}</span>
                </div>
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
        })}
      </div>
    </div>
  );
}
