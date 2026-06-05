import { useState, useEffect } from 'preact/hooks';
import { Link, useLocation } from 'wouter';
import { Menu, X, Activity, ChevronRight } from 'lucide-react';
import clsx from 'clsx';
import { BOTS, type StrategyId } from '@/types/bot';
import { useBotStatus } from '@/api/bots';

interface Props {
  active: StrategyId;
  children: any;
}

export function Layout({ active, children }: Props) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [, navigate] = useLocation();

  // Close drawer on route change
  useEffect(() => { setDrawerOpen(false); }, [active]);
  // Close drawer on ESC
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === 'Escape' && setDrawerOpen(false);
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, []);

  const activeBot = BOTS.find(b => b.id === active);

  return (
    <div class="min-h-screen flex flex-col">
      {/* ── HEADER ── */}
      <header class="sticky top-0 z-40 border-b border-bg-border bg-bg/85 backdrop-blur-md">
        <div class="max-w-7xl mx-auto px-3 md:px-6 h-14 flex items-center gap-3">
          {/* Mobile: hamburger */}
          <button
            class="md:hidden btn-ghost p-2"
            aria-label="Open menu"
            onClick={() => setDrawerOpen(true)}
          >
            <Menu size={20} />
          </button>

          {/* Logo / brand */}
          <Link to="/bots" class="flex items-center gap-2 font-bold tracking-tight">
            <div class="size-7 rounded-md bg-gradient-to-br from-accent-blue to-accent-purple flex items-center justify-center">
              <Activity size={16} class="text-white" />
            </div>
            <span class="hidden sm:inline">BTC Bots</span>
            {activeBot && (
              <span class="md:hidden text-sm text-text-muted truncate">
                · {activeBot.short}
              </span>
            )}
          </Link>

          {/* Desktop: top tabs */}
          <nav class="hidden md:flex items-center gap-1 ml-4">
            {BOTS.map(b => (
              <Link
                key={b.id}
                to={`/bots/${b.short}`}
                class={clsx(
                  'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
                  b.id === active
                    ? 'bg-bg-hover text-text'
                    : 'text-text-muted hover:bg-bg-hover hover:text-text'
                )}
              >
                {b.label}
                <span class={clsx(
                  'ml-2 pill text-[10px]',
                  b.accent === 'green' && 'pill-green',
                  b.accent === 'purple' && 'pill bg-accent-purple/15 text-accent-purple',
                  b.accent === 'blue' && 'pill-blue',
                )}>{b.badge}</span>
              </Link>
            ))}
          </nav>

          <div class="flex-1" />

          {/* Live indicator */}
          <LiveDot strategy={active} />
        </div>
      </header>

      {/* ── MOBILE DRAWER ── */}
      {drawerOpen && (
        <>
          <div
            class="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm md:hidden animate-fade-in"
            onClick={() => setDrawerOpen(false)}
          />
          <aside class="fixed left-0 top-0 z-50 h-full w-72 md:hidden bg-bg-card border-r border-bg-border shadow-2xl animate-slide-in">
            <div class="flex items-center justify-between p-4 border-b border-bg-border">
              <div class="flex items-center gap-2">
                <div class="size-7 rounded-md bg-gradient-to-br from-accent-blue to-accent-purple flex items-center justify-center">
                  <Activity size={16} class="text-white" />
                </div>
                <span class="font-bold">BTC Bots</span>
              </div>
              <button
                class="btn-ghost p-1.5"
                aria-label="Close menu"
                onClick={() => setDrawerOpen(false)}
              >
                <X size={20} />
              </button>
            </div>

            <nav class="p-2 space-y-1">
              {BOTS.map(b => (
                <button
                  key={b.id}
                  class={clsx(
                    'w-full text-left p-3 rounded-lg transition-colors flex items-start justify-between gap-3',
                    b.id === active ? 'bg-bg-hover ring-1 ring-accent-blue/30' : 'hover:bg-bg-hover'
                  )}
                  onClick={() => navigate(`/bots/${b.short}`)}
                >
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-2 mb-0.5">
                      <span class="font-semibold">{b.label}</span>
                      <span class={clsx(
                        'pill text-[10px]',
                        b.accent === 'green' && 'pill-green',
                        b.accent === 'purple' && 'pill bg-accent-purple/15 text-accent-purple',
                        b.accent === 'blue' && 'pill-blue',
                      )}>{b.badge}</span>
                    </div>
                    <p class="text-xs text-text-muted leading-relaxed">{b.description}</p>
                  </div>
                  <ChevronRight size={16} class="text-text-dim mt-1 shrink-0" />
                </button>
              ))}
            </nav>
          </aside>
        </>
      )}

      {/* ── MAIN ── */}
      <main class="flex-1 max-w-7xl w-full mx-auto px-3 md:px-6 py-4 md:py-6">
        {children}
      </main>

      <footer class="border-t border-bg-border py-3 text-center text-xs text-text-dim">
        Paper · Mainnet · BTC/USDT 5m · <a href="/dashboard.html" class="text-accent-blue hover:underline">classic dashboard</a>
      </footer>
    </div>
  );
}

function LiveDot({ strategy }: { strategy: StrategyId }) {
  const { data, isFetching, isError } = useBotStatus(strategy);
  const stale = data ? (Date.now() - new Date(data.updated_at).getTime()) / 1000 > 90 : false;
  const color = isError ? 'bg-accent-red' : stale ? 'bg-accent-orange' : 'bg-accent-green';
  const label = isError ? 'offline' : stale ? 'stale' : isFetching ? 'syncing' : 'live';
  return (
    <div class="flex items-center gap-1.5 text-xs text-text-muted">
      <span class={clsx('size-1.5 rounded-full animate-pulse-slow', color)} />
      <span class="hidden sm:inline">{label}</span>
    </div>
  );
}
