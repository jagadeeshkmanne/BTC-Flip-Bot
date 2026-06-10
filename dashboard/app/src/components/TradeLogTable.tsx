import { useMemo, useRef, useState } from 'preact/hooks';
import { useVirtualizer } from '@tanstack/react-virtual';
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import clsx from 'clsx';
import type { TradeRecord } from '@/types/bot';
import { fmtTradeTime } from '@/utils/time';

const fmtTime = (iso?: string) => fmtTradeTime(iso);
const dur = (a?: string, b?: string) => {
  if (!a || !b) return '—';
  const m = Math.round((new Date(b).getTime() - new Date(a).getTime()) / 60_000);
  return m < 60 ? `${m}m` : `${(m / 60).toFixed(1)}h`;
};

const ch = createColumnHelper<TradeRecord>();

// 2026-06-10: paper trading runs fee-free for clean math. When deploying to
// real Bybit, the bot uses 0.00055 (taker fee) — the dashboard simply
// displays whatever pnl_usd the bot records.

export function TradeLogTable({ trades }: { trades: TradeRecord[] }) {
  // 2026-06-10: count wins/losses/neutrals, default-hide neutrals (BE-DCA $0)
  // since they clutter the table without adding info beyond the summary chips.
  const [showNeutrals, setShowNeutrals] = useState(false);

  const counts = useMemo(() => {
    let wins = 0, losses = 0, neutrals = 0;
    for (const t of trades) {
      const p = t.pnl_usd ?? 0;
      if (p > 0) wins++;
      else if (p < 0) losses++;
      else neutrals++;
    }
    return { wins, losses, neutrals };
  }, [trades]);

  // Newest first, optionally filtered to non-neutral
  const data = useMemo(() => {
    const arr = showNeutrals ? trades : trades.filter(t => (t.pnl_usd ?? 0) !== 0);
    return [...arr].reverse();
  }, [trades, showNeutrals]);

  const columns = useMemo(() => [
    ch.accessor('entry_time', { header: 'Time', cell: i => <span class="text-text-muted">{fmtTime(i.getValue())}</span> }),
    ch.accessor('side', { header: 'Side', cell: i => {
      const s = i.getValue();
      return <span class={clsx('pill', s === 'LONG' ? 'pill-green' : 'pill-red')}>{s}</span>;
    } }),
    ch.accessor('reason', { header: 'Exit', cell: i => {
      const r = i.getValue();
      return <span class={clsx('pill text-[10px]', r === 'TP' ? 'pill-green' : r === 'SL' ? 'pill-red' : 'pill-muted')}>{r || '—'}</span>;
    } }),
    ch.accessor('entries', { header: 'Legs', cell: i => i.getValue() ?? '—' }),
    ch.accessor(row => row.avg_entry ?? row.entry ?? row.first_entry, {
      id: 'entry',
      header: 'Entry $',
      cell: i => <span class="font-mono">{i.getValue()?.toFixed(2) ?? '—'}</span>,
    }),
    ch.accessor('exit', { header: 'Exit $', cell: i => <span class="font-mono">{i.getValue()?.toFixed(2) ?? '—'}</span> }),
    ch.accessor('pnl_usd', { header: 'P&L $', cell: i => {
      const v = i.getValue();
      if (v == null) return '—';
      return <span class={clsx('font-mono font-semibold', v >= 0 ? 'text-accent-green' : 'text-accent-red')}>
        {v >= 0 ? '+' : ''}${v.toFixed(2)}
      </span>;
    } }),
    ch.accessor('pnl_pct', { header: 'P&L %', cell: i => {
      const v = i.getValue();
      if (v == null) return '—';
      return <span class={clsx('font-mono', v >= 0 ? 'text-accent-green' : 'text-accent-red')}>
        {v >= 0 ? '+' : ''}{v.toFixed(2)}%
      </span>;
    } }),
    ch.display({
      id: 'duration',
      header: 'Held',
      cell: ctx => <span class="text-text-muted text-xs">{dur(ctx.row.original.entry_time, ctx.row.original.exit_time)}</span>,
    }),
  ], []);

  const table = useReactTable({ data, columns, getCoreRowModel: getCoreRowModel() });

  // Virtualize rows — fast at 1000+ trades
  const parentRef = useRef<HTMLDivElement>(null);
  const rowVirtualizer = useVirtualizer({
    count: table.getRowModel().rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 40,
    overscan: 8,
  });

  if (!trades.length) {
    return <div class="card text-center text-text-muted text-sm py-8">No trades yet.</div>;
  }

  return (
    <div class="card p-0 overflow-hidden">
      <div class="px-4 py-3 border-b border-bg-border space-y-3">
        <div class="flex items-center justify-between">
          <span class="text-sm font-semibold uppercase tracking-wide text-text-muted">Trade History</span>
          <span class="text-xs text-text-dim">{trades.length} total · showing {data.length}</span>
        </div>
        {/* Summary chips: profit / loss / neutral counts */}
        <div class="flex items-center gap-2 flex-wrap">
          <div class="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-accent-green/20 border border-accent-green/50 text-sm">
            <span class="text-accent-green font-bold text-base leading-none">{counts.wins}</span>
            <span class="text-accent-green/90 font-medium">Profit</span>
          </div>
          <div class="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-accent-red/20 border border-accent-red/50 text-sm">
            <span class="text-accent-red font-bold text-base leading-none">{counts.losses}</span>
            <span class="text-accent-red/90 font-medium">Loss</span>
          </div>
          <button
            type="button"
            onClick={() => setShowNeutrals(v => !v)}
            class={clsx(
              'inline-flex items-center gap-2 px-3 py-1.5 rounded-md border text-sm transition-colors cursor-pointer',
              showNeutrals
                ? 'bg-accent-orange/25 border-accent-orange/60 text-accent-orange'
                : 'bg-accent-orange/10 border-accent-orange/30 text-accent-orange/80 hover:bg-accent-orange/20'
            )}
            title={showNeutrals ? 'Click to hide BE-DCA neutrals' : 'Click to show BE-DCA neutrals'}
          >
            <span class="font-bold text-base leading-none">{counts.neutrals}</span>
            <span class="font-medium">Neutral</span>
            <span class="text-[10px] uppercase opacity-75 ml-0.5">{showNeutrals ? 'shown' : 'hidden'}</span>
          </button>
        </div>
      </div>

      {/* ── MOBILE: condensed card list (essentials only) ── */}
      <div class="md:hidden overflow-auto" style={{ maxHeight: 'min(60vh, 500px)' }}>
        {data.map((t, i) => {
          const pnl = t.pnl_usd ?? 0;
          const pct = t.pnl_pct ?? 0;
          const positive = pnl >= 0;
          const entry = t.avg_entry ?? t.entry ?? t.first_entry;
          const exit = t.exit;
          return (
            <div key={i} class="px-3 py-3 border-b border-bg-border/40 space-y-2">
              {/* Row 1: Side, Reason, Duration + P&L (right) */}
              <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-1.5 min-w-0">
                  <span class={clsx('pill text-[10px]', t.side === 'LONG' ? 'pill-green' : 'pill-red')}>{t.side}</span>
                  <span class={clsx('pill text-[10px]', t.reason === 'TP' ? 'pill-green' : t.reason === 'SL' ? 'pill-red' : 'pill-muted')}>{t.reason || '—'}</span>
                  <span class="text-2xs text-text-dim">{(t.entries ?? 1)}leg · {dur(t.entry_time, t.exit_time)}</span>
                </div>
                <div class="text-right shrink-0">
                  <div class={clsx('text-base font-bold font-mono tabular-nums leading-none', positive ? 'text-accent-green' : 'text-accent-red')}>
                    {positive ? '+' : ''}${pnl.toFixed(2)}
                  </div>
                  <div class={clsx('text-2xs font-mono mt-0.5', positive ? 'text-accent-green' : 'text-accent-red')}>
                    {positive ? '+' : ''}{pct.toFixed(2)}%
                  </div>
                </div>
              </div>

              {/* Row 2: Entry → Exit prices + timestamp */}
              <div class="flex items-center gap-3 text-xs font-mono">
                <div>
                  <span class="text-text-dim">In </span>
                  <span class="text-text">${entry?.toFixed(2) ?? '—'}</span>
                </div>
                <span class="text-text-dim">→</span>
                <div>
                  <span class="text-text-dim">Out </span>
                  <span class="text-text">${exit?.toFixed(2) ?? '—'}</span>
                </div>
                <div class="ml-auto text-text-dim">{fmtTime(t.entry_time)}</div>
              </div>
            </div>
          );
        })}
      </div>

      {/* ── DESKTOP: full 9-col virtualized table ── */}
      <div class="hidden md:block">
        <div class="grid grid-cols-9 gap-3 px-4 py-2 text-[10px] uppercase tracking-wider text-text-dim border-b border-bg-border bg-bg/50">
          {table.getHeaderGroups()[0].headers.map(h => (
            <div key={h.id} class={clsx(h.id === 'side' && 'col-span-1', 'truncate')}>
              {flexRender(h.column.columnDef.header, h.getContext())}
            </div>
          ))}
        </div>
        <div ref={parentRef} class="overflow-auto" style={{ maxHeight: 'min(60vh, 500px)' }}>
          <div style={{ height: rowVirtualizer.getTotalSize(), width: '100%', position: 'relative' }}>
            {rowVirtualizer.getVirtualItems().map(virtualRow => {
              const row = table.getRowModel().rows[virtualRow.index];
              return (
                <div
                  key={row.id}
                  class="grid grid-cols-9 gap-3 px-4 py-2 text-sm border-b border-bg-border/40 hover:bg-bg-hover transition-colors items-center"
                  style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    width: '100%',
                    height: virtualRow.size,
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                >
                  {row.getVisibleCells().map(cell => (
                    <div key={cell.id} class="truncate">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
