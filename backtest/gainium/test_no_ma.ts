// Test: same v1 config but WITHOUT MA filter
// If trades count jumps from 3 to many → MA is over-filtering

import DCABacktesting from './src/dca'
import {
  ExchangeIntervals, ExchangeEnum, StrategyEnum,
  OrderTypeEnum, StartConditionEnum, OrderSizeTypeEnum,
  IndicatorEnum, IndicatorStartConditionEnum, IndicatorsLogicEnum,
  IndicatorAction, IndicatorSection, CloseConditionEnum,
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
const data5m = bars.filter(b => b.time >= bars[bars.length-1].time - 180 * 86400 * 1000)

const gid = uuidv4()
const bt: any = new DCABacktesting({
  exchange: ExchangeEnum.bybit,
  symbols: [{ pair: 'BTCUSDT', exchange: ExchangeEnum.bybit,
    baseAsset: { minAmount: 0.001, maxAmount: 1500, step: 0.001, name: 'BTC' },
    quoteAsset: { minAmount: 5, name: 'USDT' }, maxOrders: 200, priceAssetPrecision: 1 }],
  interval: ExchangeIntervals.fiveM,
  balances: [{ asset: 'USDT', free: '5000', locked: '0' }],
  userFee: 0.055, slippage: 0.02,
  prices: [{ symbol: 'BTCUSDT', price: data5m[data5m.length-1].close }],
  settings: {
    pair: ['BTCUSDT'], name: 'rsi-only', strategy: StrategyEnum.long,
    baseOrderSize: '100', orderSize: '100', orderSizeType: OrderSizeTypeEnum.usd,
    startOrderType: OrderTypeEnum.market, startCondition: StartConditionEnum.ti,
    useDca: true, ordersCount: '2', activeOrdersCount: '2', step: '0.5', stepScale: '1', volumeScale: '1',
    useTp: true, tpPerc: '0.4', dealCloseCondition: CloseConditionEnum.tp,
    useSl: true, slPerc: '0.6', baseSlOn: 'avg',
    moveSL: true, moveSLTrigger: '0.5', moveSLValue: '0',
    useSmartOrders: false, hodlDay: '', hodlAt: '', hodlNextBuy: 0,
    maxNumberOfOpenDeals: '1',
    profitCurrency: 'quote', orderFixedIn: 'quote',
    cooldownAfterDealStart: true, cooldownAfterDealStartUnits: 'minutes', cooldownAfterDealStartInterval: 15,
    cooldownAfterDealStop: true, cooldownAfterDealStopUnits: 'minutes', cooldownAfterDealStopInterval: 15,
    // ONLY RSI (no MA filter)
    indicators: [{
      uuid: uuidv4(), type: IndicatorEnum.rsi, indicatorLength: 9, indicatorValue: '30',
      indicatorCondition: IndicatorStartConditionEnum.lt, indicatorInterval: ExchangeIntervals.fiveM,
      indicatorAction: IndicatorAction.startDeal, groupId: gid,
    }],
    indicatorGroups: [{ id: gid, logic: IndicatorsLogicEnum.and, action: IndicatorAction.startDeal, section: IndicatorSection.controller }],
  },
  fullResult: true,
} as any)

console.log(`Testing RSI<30 ONLY (no MA filter) on ${data5m.length} bars (180d)`)
bt.test([{ interval: ExchangeIntervals.fiveM, bar: data5m }] as any)
  .then((r: any) => {
    const f = r.financial, n = r.numerical
    console.log(`\nResult:`)
    console.log(`  Total deals: ${n.all}`)
    console.log(`  Win / Loss:  ${n.profit} / ${n.loss}`)
    console.log(`  Open / Closed: ${n.open} / ${n.closed}`)
    console.log(`  Net profit: $${f.netProfitTotalUsd?.toFixed(2)} (${f.netProfitTotalPerc?.toFixed(2)}%)`)
    console.log(`  Win rate (closed): ${n.profit > 0 || n.loss > 0 ? (n.profit / (n.profit + n.loss) * 100).toFixed(1) + '%' : '—'}`)
  })
