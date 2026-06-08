import { Route, Switch, Redirect } from 'wouter';
import { Layout } from '@/components/Layout';
import { BotPage } from '@/pages/BotPage';
import { BOTS, type StrategyId } from '@/types/bot';

// short ("v1") → strategy id ("rsiscalp_trend")
const SHORT_TO_ID = Object.fromEntries(BOTS.map(b => [b.short, b.id])) as Record<string, StrategyId>;

// Backward-compat aliases — old URLs redirect to the renamed bots.
// 2026-06-08: consolidated to 2 bots, renamed v1.1→v1 (with-trend) and v3→v2 (counter-trend).
const ALIAS: Record<string, string> = {
  'v1.1': 'v1',
  'v3':   'v2',
  'v4':   'v1',   // removed bot
  'v5':   'v1',   // removed bot
};

export function App() {
  return (
    <Switch>
      <Route path="/bots/:short">
        {({ short }) => {
          // Check alias first (handles bookmarks to /bots/v1, /bots/v3, /bots/v4 etc.)
          const aliased = ALIAS[short!];
          if (aliased) return <Redirect to={`/bots/${aliased}`} />;
          const id = SHORT_TO_ID[short!];
          if (!id) return <Redirect to="/bots/v1" />;
          return (
            <Layout active={id}>
              <BotPage strategy={id} />
            </Layout>
          );
        }}
      </Route>
      <Route path="/bots"><Redirect to="/bots/v1" /></Route>
      <Route><Redirect to="/bots/v1" /></Route>
    </Switch>
  );
}
