import { Route, Switch, Redirect } from 'wouter';
import { Layout } from '@/components/Layout';
import { BotPage } from '@/pages/BotPage';
import { BOTS, type StrategyId } from '@/types/bot';

// short ("v1.1") → strategy id ("rsiscalp_trend")
const SHORT_TO_ID = Object.fromEntries(BOTS.map(b => [b.short, b.id])) as Record<string, StrategyId>;

// Backward-compat aliases (old URLs from before versioning)
const ALIAS: Record<string, string> = {
  'v1': 'v1.1', 'v2': 'v2.0', 'v5': 'v5.0',
  // Removed bots redirect to v1.1 (the winner)
  'v3': 'v1.1', 'v4': 'v1.1',
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
          if (!id) return <Redirect to="/bots/v1.1" />;
          return (
            <Layout active={id}>
              <BotPage strategy={id} />
            </Layout>
          );
        }}
      </Route>
      <Route path="/bots"><Redirect to="/bots/v1.1" /></Route>
      <Route><Redirect to="/bots/v1.1" /></Route>
    </Switch>
  );
}
