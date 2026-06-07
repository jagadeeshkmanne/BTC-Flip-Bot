// Minimal TI test - just ONE indicator (RSI ≤ 50) on 5m
// RSI < 50 happens often, should fire MANY deals
// If 0 deals = our indicator config is wrong

import DCABacktesting from './src/dca'
import {
  ExchangeIntervals, ExchangeEnum, StrategyEnum,
  OrderTypeEnum, StartConditionEnum, OrderSizeTypeEnum,
  IndicatorEnum, IndicatorStartConditionEnum, IndicatorsLogicEnum,
  IndicatorAction, IndicatorSection,
} from './src/types'
import * as fs from 'fs'
import { v4 as uuidv4 } from 'uuid'

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
const cutoff = bars[bars.length-1].time - 7 * 86400 * 1000
const data = bars.filter(b => b.time >= cutoff)
console.log(`Bars: ${data.length}`)

const gid = uuidv4()
const settings: any = {
  pair: ['BTCUSDT'],
  name: 'rsi-50-test',
  strategy: StrategyEnum.long,
  baseOrderSize: '100', orderSize: '100', orderSizeType: OrderSizeTypeEnum.usd,
  startOrderType: OrderTypeEnum.market,
  startCondition: StartConditionEnum.ti,                        // ← TI mode
  useDca: false, ordersCount: '1', activeOrdersCount: '1',
  step: '0.5', stepScale: '1', volumeScale: '1',
  useTp: true, tpPerc: '0.5',
  useSl: true, slPerc: '1.0', baseSlOn: 'avg',
  useSmartOrders: false,
  hodlDay: '', hodlAt: '', hodlNextBuy: 0,
  maxNumberOfOpenDeals: '5',
  profitCurrency: 'quote', orderFixedIn: 'quote',
  // ONLY ONE INDICATOR: RSI < 50 on 5m (very loose - should hit ~50% of time)
  indicators: [{
    uuid: uuidv4(),
    type: IndicatorEnum.rsi,
    indicatorLength: 14,
    indicatorValue: '50',
    indicatorCondition: IndicatorStartConditionEnum.lt,
    indicatorInterval: ExchangeIntervals.fiveM,
    indicatorAction: IndicatorAction.startDeal,   // ← THE MISSING FIELD
    groupId: gid,
  }],
  indicatorGroups: [{
    id: gid,
    logic: IndicatorsLogicEnum.and,
    action: IndicatorAction.startDeal,
    section: IndicatorSection.controller,
  }],
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

// DEBUG: inspect what the strategy thinks it has
console.log('Strategy:', bt.strategy?.constructor?.name)
console.log('getOtherIntervals:', bt.getOtherIntervals?.())

bt.test([{ interval: ExchangeIntervals.fiveM, bar: data }] as any)
  .then((r: any) => {
    if (!r) { console.log('null result'); return }
    console.log('\n── RESULT ──')
    console.log('deals:', r.deals?.length)
    const f = r.financial || {}
    console.log('netProfit%:', f.netProfitTotalPerc)
    console.log('netProfit$:', f.netProfitTotalUsd)
    console.log('grossProfit$:', f.grossProfitUsd, 'grossLoss$:', f.grossLossUsd)
    if (r.indicatorsEvents?.length) {
      console.log('indicatorsEvents:', r.indicatorsEvents.length)
      console.log('  sample event:', JSON.stringify(r.indicatorsEvents[0]).slice(0,200))
    }
  })
  .catch((e: any) => { console.error('ERR:', e.message); console.error(e.stack?.split('\n').slice(0,6).join('\n')) })
