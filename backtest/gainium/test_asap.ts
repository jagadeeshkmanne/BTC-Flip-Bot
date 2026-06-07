// Minimal test: ASAP start condition - should open deal IMMEDIATELY
// If this fires deals, engine works. If not, deeper config issue.

import DCABacktesting from './src/dca'
import {
  ExchangeIntervals, ExchangeEnum, StrategyEnum,
  OrderTypeEnum, StartConditionEnum, OrderSizeTypeEnum,
} from './src/types'
import * as fs from 'fs'

const CSV = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
const lines = fs.readFileSync(CSV, 'utf-8').trim().split('\n')
const bars: any[] = []
for (let i = 1; i < lines.length; i++) {
  const c = lines[i].split(',')
  bars.push({
    time: Math.floor(new Date(c[0]).getTime() / 1000) * 1000,
    open: +c[1], high: +c[2], low: +c[3], close: +c[4], volume: +c[5],
    symbol: 'BTCUSDT', isFinal: true,
  })
}
// Last 7 days
const cutoff = bars[bars.length-1].time - 7 * 86400 * 1000
const data = bars.filter(b => b.time >= cutoff)
console.log(`Bars: ${data.length}, first: ${new Date(data[0].time).toISOString()}, last: ${new Date(data[data.length-1].time).toISOString()}`)

const settings: any = {
  pair: ['BTCUSDT'],
  name: 'asap-test',
  strategy: StrategyEnum.long,
  baseOrderSize: '100', orderSize: '100', orderSizeType: OrderSizeTypeEnum.usd,
  startOrderType: OrderTypeEnum.market,
  startCondition: StartConditionEnum.asap,
  useDca: true, ordersCount: '2', activeOrdersCount: '2',
  step: '0.5', stepScale: '1', volumeScale: '1',
  useTp: true, tpPerc: '0.5',
  useSl: true, slPerc: '1.0', baseSlOn: 'avg',
  useSmartOrders: false,
  hodlDay: '', hodlAt: '', hodlNextBuy: 0,
  indicators: [], indicatorGroups: [],
  maxNumberOfOpenDeals: '5',
  profitCurrency: 'quote', orderFixedIn: 'quote',
}

const bt: any = new DCABacktesting({
  exchange: ExchangeEnum.bybit,
  symbols: [{
    pair: 'BTCUSDT', exchange: ExchangeEnum.bybit,
    baseAsset: { minAmount: 0.001, maxAmount: 1500, step: 0.001, name: 'BTC' },
    quoteAsset: { minAmount: 5, name: 'USDT' },
    maxOrders: 200, priceAssetPrecision: 1,
  }],
  interval: ExchangeIntervals.fiveM,
  balances: [{ asset: 'USDT', free: '5000', locked: '0' }],
  userFee: 0.055, slippage: 0.02,
  prices: [{ symbol: 'BTCUSDT', price: data[data.length-1].close }],
  settings,
} as any)

console.log('\nRunning ASAP backtest (should fire deals immediately)…')
const start = Date.now()
bt.test([{ interval: ExchangeIntervals.fiveM, bar: data }] as any)
  .then((r: any) => {
    console.log(`Done in ${((Date.now()-start)/1000).toFixed(1)}s`)
    if (!r) { console.log('null result'); return }
    console.log('Result keys:', Object.keys(r))
    console.log('Financial keys:', Object.keys(r.financial || {}).slice(0,20))
    console.log('\nFINANCIALS:')
    const f = r.financial || {}
    console.log('  totalDeals:', f.totalDeals)
    console.log('  netProfitTotalPerc:', f.netProfitTotalPerc)
    console.log('  netProfitTotalUsd:', f.netProfitTotalUsd)
    console.log('  deals?:', r.deals?.length)
    if (r.deals?.length) {
      console.log('  First deal:', JSON.stringify(r.deals[0]).slice(0,500))
    }
  })
  .catch((e: any) => {
    console.error('ERROR:', e.message)
    console.error(e.stack?.split('\n').slice(0,8).join('\n'))
  })
