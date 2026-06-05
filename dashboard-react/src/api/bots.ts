import { useQuery } from '@tanstack/react-query';
import type { BotState, BotStatus, StrategyId } from '@/types/bot';

// Dev: Vite proxies /api → live server. Prod: served by same server, relative path works.
const fetchJSON = async <T,>(url: string): Promise<T> => {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json() as Promise<T>;
};

export const useBotStatus = (strategy: StrategyId) =>
  useQuery({
    queryKey: ['status', strategy],
    queryFn: () => fetchJSON<BotStatus>(`/api/bot/day/status?strategy=${strategy}`),
    refetchInterval: 5_000,
  });

export const useBotState = (strategy: StrategyId) =>
  useQuery({
    queryKey: ['state', strategy],
    queryFn: () => fetchJSON<BotState>(`/api/bot/day/state?strategy=${strategy}`),
    refetchInterval: 10_000,
  });

export const useKlines = (interval: '5m' | '15m' | '1h', limit = 288) =>
  useQuery({
    queryKey: ['klines', interval, limit],
    queryFn: () => fetchJSON<any[][]>(`/api/klines?interval=${interval}&limit=${limit}`),
    refetchInterval: 30_000,
  });
