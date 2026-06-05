import { useQuery } from '@tanstack/react-query';
import type { BotState, BotStatus, StrategyId } from '@/types/bot';

const fetchJSON = async <T,>(url: string): Promise<T> => {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json() as Promise<T>;
};

// 2026-06-05: single batched endpoint that returns ALL bots' status + state.
// One HTTP request every 5s instead of 6 (3 statuses × 3 states).
export interface AllBots {
  [strategy: string]: { status: BotStatus | null; state: BotState | null };
}

export const useAllBots = () =>
  useQuery({
    queryKey: ['bots-all'],
    queryFn: () => fetchJSON<AllBots>('/api/bots/all'),
    refetchInterval: 5_000,
  });

// Per-bot hooks now derive from the batched query so all 3 bot pages share
// the SAME network request. Single source of truth.
export function useBotStatus(strategy: StrategyId) {
  const q = useAllBots();
  return {
    ...q,
    data: q.data?.[strategy]?.status ?? undefined,
  };
}

export function useBotState(strategy: StrategyId) {
  const q = useAllBots();
  return {
    ...q,
    data: q.data?.[strategy]?.state ?? undefined,
  };
}

export const useKlines = (interval: '5m' | '15m' | '1h', limit = 288) =>
  useQuery({
    queryKey: ['klines', interval, limit],
    queryFn: () => fetchJSON<any[][]>(`/api/klines?interval=${interval}&limit=${limit}`),
    refetchInterval: 30_000,
  });
