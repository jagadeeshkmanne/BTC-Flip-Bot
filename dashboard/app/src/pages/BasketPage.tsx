import clsx from 'clsx';
import { useAllBots } from '@/api/bots';
import { KpiCard } from '@/components/KpiCard';
import { STRATEGY_TO_BOT, type BasketStatus, type BotState, type StrategyId } from '@/types/bot';

const INITIAL = 5000;

export function BasketPage({ strategy = 'allweather' }: { strategy?: StrategyId }) {
  const q = useAllBots();
  const status = q.data?.[strategy]?.status as unknown as BasketStatus | undefined;
  const state = q.data?.[strategy]?.state as unknown as
    { subs?: Record<string, BotState> } | undefined;
  const bot = STRATEGY_TO_BOT[strategy];

  const equity = status?.balance ?? 0;
  const pnlUsd = equity - INITIAL;
  const pnlPct = (pnlUsd / INITIAL) * 100;
  const dd = (status?.drawdown_pct ?? 0) * 100;
  const st = status?.stats ?? { total: 0, wins: 0, losses: 0, pnl: 0 };
  const winRate = st.total > 0 ? (st.wins / st.total) * 100 : 0;
  const coins = status?.coins ?? [];

  // aggregate recent trades across the 4 sub-accounts, newest first
  const trades = Object.values(state?.subs ?? {})
    .flatMap((s: any) => (s?.trade_log ?? []).map((t: any) => ({ ...t })))
    .sort((a, b) => (b.exit_time ?? '').localeCompare(a.exit_time ?? ''))
    .slice(0, 30);

  return (
    <div class="space-y-3 md:space-y-4 min-w-0">
      {/* Strategy label */}
      <div class="min-w-0">
        <h1 class="text-sm md:text-lg font-bold tracking-tight flex items-baseline gap-2 flex-wrap">
          <span class="truncate">{bot.label}</span>
          <span class="text-2xs font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded shrink-0 bg-accent-purple/15 text-accent-purple">
            {bot.badge}
          </span>
          {status?.paper_mode && (
            <span class="text-2xs font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded shrink-0 bg-bg-hover text-text-muted">
              paper
            </span>
          )}
        </h1>
        <p class="text-2xs text-text-dim mt-1 leading-relaxed">{bot.description}</p>
      </div>

      {/* KPI strip */}
      <div class="grid grid-cols-2 md:grid-cols-4 gap-2 md:gap-3">
        <KpiCard label="Basket Equity" value={`$${equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
          delta={pnlPct} tint big accent="purple" loading={!status} />
        <KpiCard label="Total P&L" value={`$${pnlUsd.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
          hint={`from $${INITIAL.toLocaleString()}`} accent={pnlUsd >= 0 ? 'green' : 'red'} loading={!status} />
        <KpiCard label="Drawdown" value={`${dd.toFixed(1)}%`}
          hint={`peak $${(status?.peak_equity ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
          accent={dd < -10 ? 'red' : 'neutral'} loading={!status} />
        <KpiCard label="Win Rate" value={`${winRate.toFixed(0)}%`}
          hint={`${st.total} trades · ${status?.n_positions ?? 0}/4 open`} accent="blue" loading={!status} />
      </div>

      {/* Per-coin positions */}
      <div class="card overflow-hidden p-0">
        <div class="px-3 md:px-4 py-2 border-b border-bg-border text-xs uppercase tracking-wider text-text-muted font-semibold">
          Positions · equal weight (25% each)
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-2xs uppercase tracking-wider text-text-dim border-b border-bg-border">
                <th class="text-left font-semibold px-3 md:px-4 py-2">Coin</th>
                <th class="text-left font-semibold px-2 py-2">Side</th>
                <th class="text-right font-semibold px-2 py-2">Live</th>
                <th class="text-right font-semibold px-2 py-2 hidden sm:table-cell">Entry</th>
                <th class="text-right font-semibold px-2 py-2">uPnL</th>
                <th class="text-right font-semibold px-3 md:px-4 py-2">Sub-equity</th>
              </tr>
            </thead>
            <tbody class="font-mono">
              {coins.map(c => {
                // null-safe: a coin that failed to fetch (Bybit hiccup) has ok:false and no
                // live_price/sub_equity — never crash the whole page on a missing field.
                const money = (v?: number, dp = 2) =>
                  (v == null || Number.isNaN(v)) ? '—'
                    : '$' + v.toLocaleString(undefined, { maximumFractionDigits: v < 10 ? 4 : dp });
                const side = c.position?.side ?? c.signal ?? '—';
                const upnl = c.position?.unrealized_usd ?? 0;
                const stale = !c.ok;
                return (
                  <tr key={c.symbol} class={clsx('border-b border-bg-border/50 last:border-0', stale && 'opacity-50')}>
                    <td class="px-3 md:px-4 py-2 font-semibold font-sans">
                      {c.symbol.replace('USDT', '')}{stale && <span class="ml-1 text-2xs text-text-dim">(stale)</span>}
                    </td>
                    <td class="px-2 py-2">
                      <span class={clsx('pill text-[10px]',
                        side === 'LONG' ? 'pill-green' : 'pill bg-accent-red/15 text-accent-red')}>
                        {side}
                      </span>
                    </td>
                    <td class="px-2 py-2 text-right tabular-nums">{money(c.live_price)}</td>
                    <td class="px-2 py-2 text-right tabular-nums text-text-muted hidden sm:table-cell">
                      {c.position ? money(c.position.avg_entry) : '—'}
                    </td>
                    <td class={clsx('px-2 py-2 text-right tabular-nums font-semibold',
                      upnl >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                      {upnl >= 0 ? '+' : ''}{upnl.toFixed(2)}
                    </td>
                    <td class="px-3 md:px-4 py-2 text-right tabular-nums">{money(c.sub_equity)}</td>
                  </tr>
                );
              })}
              {coins.length === 0 && (
                <tr><td colSpan={6} class="px-4 py-6 text-center text-text-dim font-sans">Waiting for first bot run…</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recent trades (aggregated across coins) */}
      <div class="card overflow-hidden p-0">
        <div class="px-3 md:px-4 py-2 border-b border-bg-border text-xs uppercase tracking-wider text-text-muted font-semibold">
          Recent closed trades
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-2xs uppercase tracking-wider text-text-dim border-b border-bg-border">
                <th class="text-left font-semibold px-3 md:px-4 py-2">Side</th>
                <th class="text-right font-semibold px-2 py-2">Entry</th>
                <th class="text-right font-semibold px-2 py-2">Exit</th>
                <th class="text-right font-semibold px-3 md:px-4 py-2">Net</th>
              </tr>
            </thead>
            <tbody class="font-mono">
              {trades.map((t, i) => (
                <tr key={i} class="border-b border-bg-border/50 last:border-0">
                  <td class="px-3 md:px-4 py-2">
                    <span class={clsx('pill text-[10px]',
                      t.side === 'LONG' ? 'pill-green' : 'pill bg-accent-red/15 text-accent-red')}>
                      {t.side}
                    </span>
                  </td>
                  <td class="px-2 py-2 text-right tabular-nums text-text-muted">{t.entry?.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                  <td class="px-2 py-2 text-right tabular-nums text-text-muted">{t.exit?.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                  <td class={clsx('px-3 md:px-4 py-2 text-right tabular-nums font-semibold',
                    (t.net_usd ?? 0) >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                    {(t.net_usd ?? 0) >= 0 ? '+' : ''}{(t.net_usd ?? 0).toFixed(2)}
                  </td>
                </tr>
              ))}
              {trades.length === 0 && (
                <tr><td colSpan={4} class="px-4 py-6 text-center text-text-dim font-sans">No closed trades yet — positions opened on first run.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
