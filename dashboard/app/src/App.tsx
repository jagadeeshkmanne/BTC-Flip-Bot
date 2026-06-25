import { Route, Switch, Redirect } from 'wouter';
import { Layout } from '@/components/Layout';
import { BotPage } from '@/pages/BotPage';
import { BOTS, type StrategyId } from '@/types/bot';

// short ("v1") → strategy id ("rsiscalp_trend")
const SHORT_TO_ID = Object.fromEntries(BOTS.map(b => [b.short, b.id])) as Record<string, StrategyId>;

// Backward-compat aliases — every legacy/retired bot URL now points at btcv2,
// the single live bot (2026-06-25: trend_btc, allweather, btcalts all retired).
const ALIAS: Record<string, string> = {
  'v1': 'btcv2', 'v1.1': 'btcv2', 'v2': 'btcv2', 'v2.1': 'btcv2', 'v2.2': 'btcv2',
  'v4': 'btcv2', 'v5': 'btcv2', 'trend_btc': 'btcv2', 'allweather': 'btcv2', 'btcalts': 'btcv2',
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
          if (!id) return <Redirect to="/bots/btcv2" />;
          return (
            <Layout active={id}>
              <BotPage strategy={id} />
            </Layout>
          );
        }}
      </Route>
      <Route path="/bots"><Redirect to="/bots/btcv2" /></Route>
      <Route><Redirect to="/bots/btcv2" /></Route>
    </Switch>
  );
}
