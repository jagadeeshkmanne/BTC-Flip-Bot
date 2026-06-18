import { Route, Switch, Redirect } from 'wouter';
import { Layout } from '@/components/Layout';
import { BotPage } from '@/pages/BotPage';
import { BOTS, type StrategyId } from '@/types/bot';

// short ("v1") → strategy id ("rsiscalp_trend")
const SHORT_TO_ID = Object.fromEntries(BOTS.map(b => [b.short, b.id])) as Record<string, StrategyId>;

// Backward-compat aliases — old URLs redirect to the trend bot (the new default).
// 2026-06-16: v2.1/v2.2 retired; everything legacy points at trend_btc.
const ALIAS: Record<string, string> = {
  'v1':   'trend_btc',
  'v1.1': 'trend_btc',
  'v2':   'trend_btc',
  'v2.1': 'trend_btc',
  'v2.2': 'trend_btc',
  'v4':   'trend_btc',
  'v5':   'trend_btc',
};

export function App() {
  return (
    <Switch>
      <Route path="/bots/:short">
        {({ short }) => {
          // Check alias first (handles bookmarks to /bots/v1, /bots/v2.1 etc.)
          const aliased = ALIAS[short!];
          if (aliased) return <Redirect to={`/bots/${aliased}`} />;
          // Resolve by id (URL slug = bot id) first, then by short name as fallback.
          const id = (BOTS.find(b => b.id === short)?.id) ?? SHORT_TO_ID[short!];
          if (!id) return <Redirect to="/bots/trend_btc" />;
          return (
            <Layout active={id}>
              <BotPage strategy={id} />
            </Layout>
          );
        }}
      </Route>
      <Route path="/bots"><Redirect to="/bots/trend_btc" /></Route>
      <Route><Redirect to="/bots/trend_btc" /></Route>
    </Switch>
  );
}
