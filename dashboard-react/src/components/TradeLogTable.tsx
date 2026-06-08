import { useMemo, useRef } from 'preact/hooks';
import { useVirtualizer } from '@tanstack/react-virtual';
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import clsx from 'clsx';
import type { TradeRecord } from '@/types/bot';

const fmtTime = (iso?: string) => {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
};
const dur = (a?: string, b?: string) => {
  if (!a || !b) return '—';
  const m = Math.round((new Date(b).getTime() - new Date(a).getTime()) / 60_000);
  return m < 60 ? `${m}m` : `${(m / 60).toFixed(1)}h`;
};

const ch = createColumnHelper<TradeRecord>();

// Bybit USDT-M taker fee (used as fallback when bot doesn't store fee_usd).
const FEE_RATE = 0.00055;

function computeFees(t: TradeRecord): number {
  // Prefer bot-saved value when available
  if (t.fee_usd != null) return t.fee_usd;
  // Fallback: compute round-trip fees from entry + exit notional
  const entry = t.avg_entry ?? t.entry ?? t.first_entry;
  const exit = t.exit;
  // Bot saves as `qty_total`; older code paths may use `qty`
  const qty = t.qty_total ?? t.qty;
  if (entry == null || exit == null || qty == null) return 0;
  return (entry + exit) * qty * FEE_RATE;
}

export function TradeLogTable({ trades }: { trades: TradeRecord[] }) {
  // Newest first
  const data = useMemo(() => [...trades].reverse(), [trades]);

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
    ch.display({
      id: 'gross',
      header: 'Gross $',
      cell: ctx => {
        const t = ctx.row.original;
        const net = t.pnl_usd;
        const fees = computeFees(t);
        if (net == null) return '—';
        const gross = net + fees;
        return <span class={clsx('font-mono text-xs', gross >= 0 ? 'text-accent-green/70' : 'text-accent-red/70')}>
          {gross >= 0 ? '+' : ''}${gross.toFixed(2)}
        </span>;
      },
    }),
    ch.display({
      id: 'fee',
      header: 'Fee $',
      cell: ctx => {
        const fees = computeFees(ctx.row.original);
        return <span class="font-mono text-xs text-text-dim">-${fees.toFixed(2)}</span>;
      },
    }),
    ch.accessor('pnl_usd', { header: 'Net $', cell: i => {
      const v = i.getValue();
      if (v == null) return '—';
      return <span class={clsx('font-mono font-semibold', v >= 0 ? 'text-accent-green' : 'text-accent-red')}>
        {v >= 0 ? '+' : ''}${v.toFixed(2)}
      </span>;
    } }),
    ch.accessor('pnl_pct', { header: 'Net %', cell: i => {
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
      <div class="px-4 py-3 border-b border-bg-border flex items-center justify-between">
        <span class="text-sm font-semibold uppercase tracking-wide text-text-muted">Trade History</span>
        <span class="text-xs text-text-dim">{trades.length} trades</span>
      </div>

      {/* ── MOBILE: condensed card list (essentials only) ── */}
      <div class="md:hidden overflow-auto" style={{ maxHeight: 'min(60vh, 500px)' }}>
        {data.map((t, i) => {
          const pnl = t.pnl_usd ?? 0;
          const pct = t.pnl_pct ?? 0;
          const fees = computeFees(t);
          const gross = pnl + fees;
          const positive = pnl >= 0;
          const entry = t.avg_entry ?? t.entry ?? t.first_entry;
          const exit = t.exit;
          return (
            <div key={i} class="px-3 py-3 border-b border-bg-border/40 space-y-2">
              {/* Row 1: Side, Reason, Duration + Net P&L (right) */}
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

              {/* Row 2: Entry → Exit prices */}
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

              {/* Row 3: Gross | Fee | Net breakdown */}
              <div class="flex items-center gap-3 text-2xs font-mono text-text-muted">
                <div>
                  Gross <span class={clsx(gross >= 0 ? 'text-accent-green/80' : 'text-accent-red/80')}>
                    {gross >= 0 ? '+' : ''}${gross.toFixed(2)}
                  </span>
                </div>
                <div>
                  Fee <span class="text-text-dim">-${fees.toFixed(2)}</span>
                </div>
                <div>
                  = Net <span class={clsx(positive ? 'text-accent-green' : 'text-accent-red')}>
                    {positive ? '+' : ''}${pnl.toFixed(2)}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* ── DESKTOP: full 9-col virtualized table ── */}
      <div class="hidden md:block">
        <div class="grid grid-cols-11 gap-3 px-4 py-2 text-[10px] uppercase tracking-wider text-text-dim border-b border-bg-border bg-bg/50">
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
                  class="grid grid-cols-11 gap-3 px-4 py-2 text-sm border-b border-bg-border/40 hover:bg-bg-hover transition-colors items-center"
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
