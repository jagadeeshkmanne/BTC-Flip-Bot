import { useState, useEffect } from 'preact/hooks';

// The daily candle closes at 00:00 UTC — that's when the daily MACD updates and
// the short can trigger. This shows a live countdown to the next daily close.
function msToNextUtcMidnight(): number {
  const now = new Date();
  const next = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1, 0, 0, 0, 0);
  return next - now.getTime();
}

export function DailyCloseCountdown({ shortTrigger, longTrigger }: { shortTrigger?: number | null; longTrigger?: number | null }) {
  const [ms, setMs] = useState(msToNextUtcMidnight());
  useEffect(() => {
    const t = setInterval(() => setMs(msToNextUtcMidnight()), 1000);
    return () => clearInterval(t);
  }, []);
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1_000);
  const pad = (n: number) => String(n).padStart(2, '0');
  const fmt = (n?: number | null) => (n != null ? `$${Math.round(n).toLocaleString()}` : '—');

  return (
    <div class="card flex flex-wrap items-center justify-between gap-3 p-3 md:p-4">
      <div>
        <div class="text-2xs md:text-xs uppercase tracking-wide text-text-dim">Next daily candle close · 00:00 UTC</div>
        <div class="text-xs text-text-muted">The daily MACD only updates at the daily close — the short can trigger only then.</div>
      </div>
      <div class="text-right">
        <div class="font-mono text-2xl md:text-3xl font-bold tabular-nums text-text-bright">
          {h}h {pad(m)}m {pad(s)}s
        </div>
        <div class="text-2xs md:text-xs text-text-muted mt-0.5">
          short if close &lt; <span class="text-accent-red font-semibold">{fmt(shortTrigger)}</span>
          {longTrigger != null && <> · long if &gt; <span class="text-accent-green font-semibold">{fmt(longTrigger)}</span></>}
        </div>
      </div>
    </div>
  );
}
