import { useEffect, useState, useRef } from 'preact/hooks';
import { createChart, ColorType, LineStyle, CrosshairMode, type IChartApi, type ISeriesApi } from 'lightweight-charts';
import { useKlines } from '@/api/bots';
import { useTickerStore } from '@/hooks/useBtcStream';
import clsx from 'clsx';

// Timeframe → seconds (so we know when a new bar starts). btcv2 is a 4h/daily bot.
const TF_SECONDS: Record<string, number> = { '4h': 14400, '1d': 86400 };

// EMA — matches pandas .ewm(span=n, adjust=False).mean() (the bot's formula).
function ema(closes: number[], n: number): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(null);
  if (closes.length === 0) return out;
  const k = 2 / (n + 1);
  let prev = closes[0];
  out[0] = prev;
  for (let i = 1; i < closes.length; i++) {
    prev = closes[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

type TF = '4h' | '1d';
const TIMEFRAMES: TF[] = ['4h', '1d'];

export function PriceChart() {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const latestCandleRef = useRef<{ time: any; open: number; high: number; low: number; close: number } | null>(null);
  const didInitialFitRef = useRef(false);
  const [tf, setTf] = useState<TF>('4h');

  useEffect(() => { didInitialFitRef.current = false; }, [tf]);

  // 600 bars: 4h → 100 days, 1d → ~2yr — plenty for EMA200.
  const { data: klinesMain } = useKlines(tf, 600);

  // Init chart once.
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#9ca3af',
        fontFamily: 'Inter, system-ui, sans-serif',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1f2937', style: LineStyle.Dotted },
        horzLines: { color: '#1f2937', style: LineStyle.Dotted },
      },
      timeScale: {
        borderColor: '#1f2937',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 12,
        barSpacing: 8,
        minBarSpacing: 0.5,
        lockVisibleTimeRangeOnResize: false,
        rightBarStaysOnScroll: true,
      },
      rightPriceScale: {
        borderColor: '#1f2937',
        scaleMargins: { top: 0.08, bottom: 0.08 },
        minimumWidth: 60,
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#3b82f6', width: 1, labelBackgroundColor: '#3b82f6' },
        horzLine: { color: '#3b82f6', width: 1, labelBackgroundColor: '#3b82f6' },
      },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: { time: true, price: true } },
      kineticScroll: { touch: true, mouse: false },
    });
    chartRef.current = chart;
    const ro = new ResizeObserver(entries => {
      const e = entries[0];
      chart.applyOptions({ width: e.contentRect.width, height: e.contentRect.height });
    });
    ro.observe(containerRef.current);
    return () => { ro.disconnect(); chart.remove(); chartRef.current = null; };
  }, []);

  const candleDataRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const ema50Ref = useRef<ISeriesApi<'Line'> | null>(null);
  const ema200Ref = useRef<ISeriesApi<'Line'> | null>(null);

  // Candles + EMA50/EMA200 on the SELECTED timeframe (the bot's actual trend gate:
  // EMA50 above EMA200 = uptrend → long allowed; below = downtrend).
  useEffect(() => {
    if (!chartRef.current || !klinesMain?.length) return;
    if (!candleDataRef.current) {
      candleDataRef.current = chartRef.current.addCandlestickSeries({
        upColor: '#0ecb81', downColor: '#f6465d',
        borderUpColor: '#0ecb81', borderDownColor: '#f6465d',
        wickUpColor: '#0ecb81', wickDownColor: '#f6465d',
        priceFormat: { type: 'price', precision: 0, minMove: 1 },
      });
      ema50Ref.current = chartRef.current.addLineSeries({
        color: '#fcd535', lineWidth: 2, priceLineVisible: false, lastValueVisible: false, title: 'EMA50',
      });
      ema200Ref.current = chartRef.current.addLineSeries({
        color: '#e879f9', lineWidth: 2, priceLineVisible: false, lastValueVisible: false, title: 'EMA200',
      });
    }
    const candles = klinesMain.map(k => ({
      time: Math.floor(+k[0] / 1000) as any,
      open: +k[1], high: +k[2], low: +k[3], close: +k[4],
    }));
    candleDataRef.current.setData(candles);
    const closes = klinesMain.map(k => +k[4]);
    const e50 = ema(closes, 50);
    const e200 = ema(closes, 200);
    const times = candles.map(c => c.time);
    ema50Ref.current!.setData(times.map((t, i) => e50[i] != null ? { time: t, value: e50[i]! } : null).filter(Boolean) as any);
    ema200Ref.current!.setData(times.map((t, i) => e200[i] != null ? { time: t, value: e200[i]! } : null).filter(Boolean) as any);
    latestCandleRef.current = candles[candles.length - 1] ?? null;
    if (!didInitialFitRef.current) {
      const ts = chartRef.current.timeScale();
      ts.scrollToRealTime();
      requestAnimationFrame(() => {
        const cur = ts.getVisibleLogicalRange();
        if (cur) ts.setVisibleLogicalRange({ from: cur.to - 120, to: cur.to });
      });
      didInitialFitRef.current = true;
    }
  }, [klinesMain]);

  // Live ticker → patch the last candle in real-time.
  const livePrice = useTickerStore(s => s.price);
  useEffect(() => {
    if (!livePrice || !candleDataRef.current || !latestCandleRef.current || !chartRef.current) return;
    const tfSec = TF_SECONDS[tf] ?? 14400;
    const nowSec = Math.floor(Date.now() / 1000);
    const currentBarTime = Math.floor(nowSec / tfSec) * tfSec;
    const last = latestCandleRef.current;
    if (currentBarTime > last.time) {
      const newBar = { time: currentBarTime as any, open: livePrice, high: livePrice, low: livePrice, close: livePrice };
      candleDataRef.current.update(newBar);
      latestCandleRef.current = newBar;
      const range = chartRef.current.timeScale().getVisibleRange();
      if (range && (Number(range.to) - Number(last.time)) < tfSec * 10) {
        chartRef.current.timeScale().scrollToRealTime();
      }
    } else {
      const updated = {
        time: last.time, open: last.open,
        high: Math.max(last.high, livePrice), low: Math.min(last.low, livePrice), close: livePrice,
      };
      candleDataRef.current.update(updated);
      latestCandleRef.current = updated;
    }
  }, [livePrice, tf]);

  return (
    <div class="card p-0 overflow-hidden">
      <div class="section-head">
        <div class="flex items-center gap-3">
          <span class="section-title">BTCUSDT</span>
          <div class="flex items-center gap-0.5 bg-bg p-0.5 rounded">
            {TIMEFRAMES.map(t => (
              <button
                key={t}
                onClick={() => setTf(t)}
                class={clsx(
                  'px-2 py-0.5 rounded text-xs font-medium transition-colors',
                  tf === t ? 'bg-bg-hover text-text' : 'text-text-muted hover:text-text'
                )}
              >{t}</button>
            ))}
          </div>
        </div>
        <span class="text-2xs text-text-dim hidden md:inline">
          <span style="color:#fcd535">━━ EMA50</span> · <span style="color:#e879f9">━━ EMA200</span> ·
          gold above magenta = uptrend (long) · drag to pan
        </span>
      </div>
      <div ref={containerRef} class="w-full" style={{ height: 'min(55vh, 500px)' }} />
    </div>
  );
}
