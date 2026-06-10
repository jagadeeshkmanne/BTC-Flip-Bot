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
  const peak = state?.peak_equity ?? status?.peak_equity ?? balance;
  const realizedUsd = balance - INITIAL;
  const realizedPct = (realizedUsd / INITIAL) * 100;
  const stats = state?.stats ?? status?.stats ?? { total: 0, wins: 0, pnl: 0 };
  // 2026-06-10: count BE-DCA exits as NEUTRAL ($0 net), not losses.
  // For old states that don't track neutrals/losses yet, derive them from trade_log.
  const tlog = (state as any)?.trade_log ?? state?.trade_log ?? [];
  const losses = (stats as any).losses ?? tlog.filter((t: any) => (t.pnl_usd ?? 0) < 0).length;
  const neutrals = (stats as any).neutrals ?? tlog.filter((t: any) => (t.pnl_usd ?? 0) === 0).length;
  const realWins = (stats as any).wins ?? tlog.filter((t: any) => (t.pnl_usd ?? 0) > 0).length;
  const winLossBase = realWins + losses;
  // True WR excludes neutrals from denominator
  const wr = winLossBase > 0 ? (realWins / winLossBase) * 100 : 0;

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

  // Drawdown — current equity vs peak (includes unrealized for live DD view).
  // If equity is at/above peak we're not in drawdown — clamp to 0 and update
  // the displayed peak so the label doesn't show "+0.54% peak $5,028" while
  // live equity is actually $5,055.
  const liveEquity = balance + unrealizedUsd;
  const displayedPeak = Math.max(peak, liveEquity);
  const ddUsd = Math.min(0, liveEquity - displayedPeak);
  const ddPct = displayedPeak > 0 ? (ddUsd / displayedPeak) * 100 : 0;

  // Days since first trade (or current day if no trades yet)
  const trades = (state as any)?.closed_trades ?? state?.trade_log ?? [];
  const firstTradeTime = trades.length > 0
    ? new Date(trades[0].entry_time || trades[0].close_time || trades[0].exit_time).getTime()
    : Date.now();
  const daysSinceStart = Math.max(1, (Date.now() - firstTradeTime) / 86_400_000);
  const avgDailyUsd = realizedUsd / daysSinceStart;
  const avgDailyPct = (avgDailyUsd / INITIAL) * 100;

  return (
    <div class="card-elev px-3 md:px-5 py-3">
      {/* Mobile: 2-col grid. Desktop: horizontal flex with dividers. */}
      <div class="grid grid-cols-2 gap-y-3 gap-x-4 md:flex md:items-center md:flex-wrap md:gap-x-8 md:gap-y-3">
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
          label="P&L %"
          value={fmtPct(totalPct)}
          valueTone={totalUsd}
          sub={`${fmtSign(totalUsd)} total`}
        />
        <Divider />
        <Metric
          label="Avg Daily P&L"
          value={trades.length > 0 ? fmtPct(avgDailyPct) : '—'}
          valueTone={avgDailyUsd}
          sub={trades.length > 0
            ? `${fmtSign(avgDailyUsd)}/day · ${daysSinceStart.toFixed(1)}d`
            : 'no trades yet'}
        />
        <Divider />
        <Metric
          label="Drawdown"
          value={ddPct < 0 ? fmtPct(ddPct) : '0.00%'}
          valueTone={ddPct < 0 ? -1 : 0}
          sub={`peak ${fmtUsd(displayedPeak)}`}
        />
        <Divider />
        <Metric
          label="Win Rate"
          value={`${wr.toFixed(0)}%`}
          sub={neutrals > 0
            ? `${realWins}W / ${losses}L / ${neutrals}N`
            : `${realWins} / ${stats.total} trades`}
        />
      </div>
    </div>
  );
}

// Divider only shows on desktop (md+). Mobile uses grid spacing.
function Divider() {
  return <div class="hidden md:block h-8 w-px bg-bg-border" />;
}

function Metric({ label, value, sub, valueTone, big }: {
  label: string; value: string; sub?: string; valueTone?: number; big?: boolean;
}) {
  const toneCls = valueTone == null ? ''
    : valueTone >= 0 ? 'text-accent-green' : 'text-accent-red';
  return (
    <div class="flex flex-col leading-tight min-w-0">
      <span class="text-2xs uppercase tracking-wider text-text-muted font-semibold truncate">{label}</span>
      <span class={clsx(
        big ? 'text-base md:text-xl lg:text-2xl' : 'text-sm md:text-lg lg:text-xl',
        'font-bold font-mono tabular-nums tracking-tight truncate',
        toneCls,
      )}>{value}</span>
      {sub && <span class="text-2xs text-text-muted font-mono mt-0.5 truncate">{sub}</span>}
    </div>
  );
}
