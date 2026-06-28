import clsx from 'clsx';
import { useTickerStore } from '@/hooks/useBtcStream';
import { useTickers } from '@/api/bots';
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
  const tickerPrice = useTickerStore(s => s.price);   // real-time WS price (BTC)
  const tickers = useTickers();                        // per-coin Bybit prices (BTC/ETH/BNB/SOL)
  const balance = state?.balance ?? status?.balance ?? 0;
  const peak = state?.peak_equity ?? status?.peak_equity ?? balance;
  const stats = state?.stats ?? status?.stats ?? { total: 0, wins: 0, pnl: 0 };
  // Realized = CLOSED-trade P&L only (the bot tracks this in stats.pnl). The open
  // trade's fees/price live in Unrealized — not Realized.
  const realizedUsd = (stats as any).pnl ?? (balance - INITIAL);
  const realizedPct = (realizedUsd / INITIAL) * 100;
  // 2026-06-10: count BE-DCA exits as NEUTRAL ($0 net), not losses.
  // Bot's trade_log uses `net_usd`; older states used `pnl_usd` — accept either.
  const tlog = (state as any)?.trade_log ?? state?.trade_log ?? [];
  const tpnl = (t: any) => t.pnl_usd ?? t.net_usd ?? 0;
  const losses = (stats as any).losses ?? tlog.filter((t: any) => tpnl(t) < 0).length;
  const neutrals = (stats as any).neutrals ?? tlog.filter((t: any) => tpnl(t) === 0).length;
  const realWins = (stats as any).wins ?? tlog.filter((t: any) => tpnl(t) > 0).length;
  const winLossBase = realWins + losses;
  // True WR excludes neutrals from denominator
  const wr = winLossBase > 0 ? (realWins / winLossBase) * 100 : 0;

  // Position can be on a DIFFERENT instrument than BTC (btcv2 shorts ETH). Read it
  // from state OR status, tolerate both field schemas (qty_total/avg_entry OR qty/entry),
  // and price it against ITS OWN instrument — not the BTC stream.
  const pos: any = (state as any)?.position ?? status?.position ?? null;
  const qty = pos ? (pos.qty_total ?? pos.qty ?? 0) : 0;
  const entry = pos ? (pos.avg_entry ?? pos.entry ?? 0) : 0;
  const inst: string = pos ? (pos.inst ?? (status as any)?.symbol ?? 'BTCUSDT') : 'BTCUSDT';
  const live = (inst === 'BTCUSDT' ? (tickerPrice || tickers.data?.[inst]) : tickers.data?.[inst])
    || (inst === (status as any)?.short_symbol ? (status as any)?.eth_price : status?.live_price) || 0;
  // Price-only unrealized (the position's mark-to-market move since entry).
  const sign = pos?.side === 'LONG' ? 1 : -1;
  const unrealizedPrice = (pos && qty && entry && live) ? qty * (live - entry) * sign : 0;
  // Live equity = bot's recorded balance (= equity at entry, incl. fees) + price move.
  const liveEquity = balance + unrealizedPrice;
  const totalUsd = liveEquity - INITIAL;
  // Unrealized P&L of the OPEN trade = everything not yet realized (incl. its fees).
  const unrealizedUsd = pos ? (totalUsd - realizedUsd) : 0;
  const unrealizedPct = (pos && entry && live) ? ((live - entry) / entry) * 100 * sign : 0;
  const totalPct = (totalUsd / INITIAL) * 100;

  // Drawdown — current LIVE equity (incl. unrealized) vs peak. Clamp to 0 at/above peak.
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
        <Metric label="Balance" big value={fmtUsd(liveEquity)} sub={`from ${fmtUsd(INITIAL, 0)}`} />
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
