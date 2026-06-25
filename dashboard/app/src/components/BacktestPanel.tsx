interface Backtest {
  config: string; window: string;
  start_usd: number; final_usd: number;
  cagr_pct: number; maxdd_pct: number; ret_dd: number; sharpe: number;
  caveat: string;
  years: { year: number; ret_pct: number; end_usd: number; maxdd_pct: number }[];
  monthly: Record<string, Record<string, number>>;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function monthCls(v: number | undefined): string {
  if (v == null) return 'text-text-dim';
  if (v > 0) return v >= 30 ? 'bg-accent-green/30 text-accent-green' : 'bg-accent-green/15 text-accent-green';
  if (v < 0) return v <= -10 ? 'bg-accent-red/30 text-accent-red' : 'bg-accent-red/15 text-accent-red';
  return 'text-text-muted';
}

export function BacktestPanel({ backtest }: { backtest?: Backtest }) {
  if (!backtest) return null;
  const stats: [string, string][] = [
    ['Start → Final', `$${backtest.start_usd.toLocaleString()} → $${backtest.final_usd.toLocaleString()}`],
    ['CAGR', `${backtest.cagr_pct}%`],
    ['Max DD', `${backtest.maxdd_pct}%`],
    ['ret/DD', `${backtest.ret_dd}`],
    ['Sharpe', `${backtest.sharpe}`],
  ];
  return (
    <div class="space-y-4 rounded-lg border border-bg-border bg-bg-card p-4">
      <div class="flex items-baseline justify-between flex-wrap gap-2">
        <h2 class="text-base md:text-lg font-bold text-text-bright">Backtest <span class="text-xs font-normal text-text-dim">· {backtest.window}</span></h2>
        <span class="text-xs text-text-dim uppercase tracking-wider">Paper · reference only</span>
      </div>
      <p class="text-xs md:text-sm text-text-dim leading-relaxed">{backtest.config}</p>

      <div class="grid grid-cols-2 md:grid-cols-5 gap-2">
        {stats.map(([k, v]) => (
          <div class="rounded bg-bg-subtle p-3">
            <div class="text-2xs md:text-xs uppercase tracking-wide text-text-dim">{k}</div>
            <div class="text-sm md:text-base font-bold tabular-nums text-text-bright">{v}</div>
          </div>
        ))}
      </div>

      <div class="overflow-x-auto">
        <div class="text-xs uppercase tracking-wide text-text-dim mb-2">Month-by-month return %</div>
        <table class="w-full text-xs md:text-sm tabular-nums border-collapse">
          <thead>
            <tr class="text-text-dim">
              <th class="text-left px-2 py-1.5">Year</th>
              {MONTHS.map((m) => <th class="px-1 py-1.5 text-center">{m}</th>)}
              <th class="px-2 py-1.5 text-right">Year</th>
            </tr>
          </thead>
          <tbody>
            {backtest.years.map((y) => (
              <tr class="border-t border-bg-border">
                <td class="px-2 py-1.5 font-bold text-text-muted">{y.year}</td>
                {MONTHS.map((_, i) => {
                  const v = backtest.monthly[String(y.year)]?.[String(i + 1)];
                  return (
                    <td class="px-1 py-1 text-center">
                      <span class={`inline-block w-full rounded px-1.5 py-0.5 ${monthCls(v)}`}>
                        {v == null ? '·' : (v > 0 ? '+' : '') + v}
                      </span>
                    </td>
                  );
                })}
                <td class={`px-2 py-1.5 text-right font-bold ${y.ret_pct >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
                  {y.ret_pct > 0 ? '+' : ''}{y.ret_pct}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p class="text-xs leading-relaxed text-accent-orange">⚠ {backtest.caveat}</p>
    </div>
  );
}
