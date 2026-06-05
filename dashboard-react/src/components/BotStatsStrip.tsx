import clsx from 'clsx';
import { useTickerStore } from '@/hooks/useBtcStream';
import type { BotStatus, BotState } from '@/types/bot';

const INITIAL = 5000;
const fmtUsd  = (n: number, d = 2) => '$' + n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtSign = (n: number, d = 2) => (n >= 0 ? '+' : '') + fmtUsd(n, d);
const fmtPct  = (n: number, d = 2) => (n >= 0 ? '+' : '') + n.toFixed(d) + '%';

/**
 * Compact Binance-style horizontal stats strip — replaces the 4-6 cards
 * with a single row of dense, high-typography metrics.
 *
 * When position open: shows Realized + Unrealized + Total side-by-side.
 * When flat: shows Balance + Realized + Win Rate + Trade count.
 */
export function BotStatsStrip({ status, state }: { status?: BotStatus; state?: BotState }) {
  const tickerPrice = useTickerStore(s => s.price);   // real-time WS price
  const balance = state?.balance ?? status?.balance ?? 0;
  const realizedUsd = balance - INITIAL;
  const realizedPct = (realizedUsd / INITIAL) * 100;
  const stats = state?.stats ?? status?.stats ?? { total: 0, wins: 0, pnl: 0 };
  const wr = stats.total > 0 ? (stats.wins / stats.total) * 100 : 0;

  const pos = status?.position;
  const live = tickerPrice || status?.live_price || 0;
  let unrealizedUsd = 0, unrealizedPct = 0;
  if (pos && pos.qty_total && pos.avg_entry && live) {
    const sign = pos.side === 'LONG' ? 1 : -1;
    unrealizedUsd = pos.qty_total * (live - pos.avg_entry) * sign;
    unrealizedPct = ((live - pos.avg_entry) / pos.avg_entry) * 100 * sign;
  }
  const totalUsd = realizedUsd + unrealizedUsd;
  const totalPct = (totalUsd / INITIAL) * 100;

  return (
    <div class="card-elev px-4 md:px-5 py-3">
      <div class="flex items-center flex-wrap gap-x-8 gap-y-3">
        <Metric label="Balance" big value={fmtUsd(balance)} sub={`from ${fmtUsd(INITIAL, 0)}`} />
        <Divider />
        <Metric
          label="Total P&L" big
          value={fmtSign(totalUsd)} valueTone={totalUsd}
          sub={fmtPct(totalPct)}
        />
        {pos && (
          <>
            <Divider />
            <Metric
              label="Unrealized" big
              value={fmtSign(unrealizedUsd)} valueTone={unrealizedUsd}
              sub={`${fmtPct(unrealizedPct)} · live`}
            />
          </>
        )}
        <Divider />
        <Metric
          label="Realized"
          value={fmtSign(realizedUsd)} valueTone={realizedUsd}
          sub={`${fmtPct(realizedPct)} · closed`}
        />
        <Divider />
        <Metric
          label="Win Rate"
          value={`${wr.toFixed(0)}%`}
          sub={`${stats.wins} / ${stats.total} trades`}
        />
      </div>
    </div>
  );
}

function Divider() {
  return <div class="h-8 w-px bg-bg-border" />;
}

function Metric({ label, value, sub, valueTone, big }: {
  label: string; value: string; sub?: string; valueTone?: number; big?: boolean;
}) {
  const toneCls = valueTone == null ? ''
    : valueTone >= 0 ? 'text-accent-green' : 'text-accent-red';
  return (
    <div class="flex flex-col leading-tight min-w-0">
      <span class="text-2xs uppercase tracking-wider text-text-dim font-medium">{label}</span>
      <span class={clsx(
        big ? 'text-xl md:text-2xl' : 'text-lg md:text-xl',
        'font-bold font-mono tabular-nums tracking-tight',
        toneCls,
      )}>{value}</span>
      {sub && <span class="text-2xs text-text-dim font-mono mt-0.5">{sub}</span>}
    </div>
  );
}
