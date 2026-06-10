import { useEffect } from 'preact/hooks';
import { create } from 'zustand';

/**
 * Real-time BTC/USDT price stream via Bybit V5 WebSocket.
 *  wss://stream.bybit.com/v5/public/linear  (USDT-margined perp, same instrument the bots trade)
 *
 * Subscribes to:
 *   tickers.BTCUSDT — push of {lastPrice, prevPrice24h, ...} on every tick
 *
 * Why Bybit (not Binance fstream): user moving fleet to Bybit. Bybit reachable from
 * everywhere (no mobile-carrier blocks the way Binance fstream sometimes is).
 *
 * Zustand store so every component reads the SAME tick — single WS connection.
 */
interface TickerState {
  price: number;
  prev24h: number;
  change24h: number;
  change24hPct: number;
  high24h: number;
  low24h: number;
  volume24h: number;
  ts: number;
  connected: boolean;
  flash: 'up' | 'down' | null;  // briefly set on every tick for flash animation
  setTicker: (t: Partial<TickerState>) => void;
  setConnected: (c: boolean) => void;
}

export const useTickerStore = create<TickerState>((set, get) => ({
  price: 0, prev24h: 0, change24h: 0, change24hPct: 0,
  high24h: 0, low24h: 0, volume24h: 0, ts: 0,
  connected: false, flash: null,
  setTicker: (t) => {
    const prev = get().price;
    const newPrice = t.price ?? prev;
    const flash: 'up' | 'down' | null = !prev || newPrice === prev ? null : newPrice > prev ? 'up' : 'down';
    set({ ...t, flash });
    if (flash) {
      // Auto-clear flash after 600ms (matches CSS animation duration)
      setTimeout(() => {
        if (get().flash === flash) set({ flash: null });
      }, 600);
    }
  },
  setConnected: (c) => set({ connected: c }),
}));

let wsInstance: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

function connect() {
  if (wsInstance && wsInstance.readyState === WebSocket.OPEN) return;
  try {
    const ws = new WebSocket('wss://stream.bybit.com/v5/public/linear');
    wsInstance = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ op: 'subscribe', args: ['tickers.BTCUSDT'] }));
      useTickerStore.getState().setConnected(true);
    };
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.topic !== 'tickers.BTCUSDT' || !msg.data) return;
        const d = msg.data;
        // 2026-06-05 fix: Bybit V5 sends BOTH 'snapshot' (full state) and
        // 'delta' (only changed fields) messages. Deltas often omit
        // high/low/volume — must NOT overwrite cached snapshot values with
        // current price as a fallback. Only include fields that are actually
        // present in this message.
        const patch: any = { ts: Date.now() };
        if (d.lastPrice != null) patch.price = +d.lastPrice;
        if (d.prevPrice24h != null) patch.prev24h = +d.prevPrice24h;
        if (d.highPrice24h != null) patch.high24h = +d.highPrice24h;
        if (d.lowPrice24h != null) patch.low24h = +d.lowPrice24h;
        if (d.volume24h != null) patch.volume24h = +d.volume24h;
        // Recompute derived 24h-change if we have both pieces (from this msg
        // OR previously cached).
        const cur = useTickerStore.getState();
        const px = patch.price ?? cur.price;
        const p24 = patch.prev24h ?? cur.prev24h;
        if (px > 0 && p24 > 0) {
          patch.change24h = px - p24;
          patch.change24hPct = ((px - p24) / p24) * 100;
        }
        if (patch.price == null) return;  // nothing useful in this msg
        useTickerStore.getState().setTicker(patch);
      } catch {}
    };
    ws.onclose = () => {
      useTickerStore.getState().setConnected(false);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, 3000);
    };
    ws.onerror = () => {
      try { ws.close(); } catch {}
    };
  } catch {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 3000);
  }
}

/** Mount once at app root. Subsequent calls are no-ops (single WS). */
export function useBtcStream() {
  useEffect(() => {
    connect();
    // Don't disconnect on unmount — keep the stream alive across page navigation
    return () => {};
  }, []);
}
