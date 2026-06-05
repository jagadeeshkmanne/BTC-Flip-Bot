import { Route, Switch, Redirect } from 'wouter';
import { Layout } from '@/components/Layout';
import { BotPage } from '@/pages/BotPage';
import { BOTS, type StrategyId } from '@/types/bot';

// short ("v1") → strategy id ("rsiscalp_trend")
const SHORT_TO_ID = Object.fromEntries(BOTS.map(b => [b.short, b.id])) as Record<string, StrategyId>;

export function App() {
  return (
    <Switch>
      <Route path="/bots/:short">
        {({ short }) => {
          const id = SHORT_TO_ID[short!];
          if (!id) return <Redirect to="/bots/v1" />;
          return (
            <Layout active={id}>
              <BotPage strategy={id} />
            </Layout>
          );
        }}
      </Route>
      <Route path="/bots"><Redirect to="/bots/v2" /></Route>
      <Route><Redirect to="/bots/v2" /></Route>
    </Switch>
  );
}
