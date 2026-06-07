import DCABacktesting from './src/dca'
import { ExchangeIntervals, ExchangeEnum, StrategyEnum, OrderTypeEnum, StartConditionEnum, OrderSizeTypeEnum, IndicatorEnum, IndicatorStartConditionEnum, IndicatorsLogicEnum, IndicatorAction, IndicatorSection, CloseConditionEnum } from './src/types'
import * as fs from 'fs'
import { v4 as uuidv4 } from 'uuid'
const CSV = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
const lines = fs.readFileSync(CSV, 'utf-8').trim().split('\n')
const bars: any[] = []
for (let i = 1; i < lines.length; i++) {
  const c = lines[i].split(',')
  bars.push({ time: Math.floor(new Date(c[0]).getTime() / 1000) * 1000, open: +c[1], high: +c[2], low: +c[3], close: +c[4], volume: +c[5], symbol: 'BTCUSDT', isFinal: true })
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
    pair: ['BTCUSDT'], name: 'inspect', strategy: StrategyEnum.long,
    baseOrderSize: '100', orderSize: '100', orderSizeType: OrderSizeTypeEnum.usd,
    startOrderType: OrderTypeEnum.market, startCondition: StartConditionEnum.ti,
    useDca: true, ordersCount: '2', activeOrdersCount: '2', step: '0.5', stepScale: '1', volumeScale: '1',
    useTp: true, tpPerc: '0.4', dealCloseCondition: CloseConditionEnum.tp,
    useSl: true, slPerc: '0.6', baseSlOn: 'avg',
    useSmartOrders: false, hodlDay: '', hodlAt: '', hodlNextBuy: 0,
    maxNumberOfOpenDeals: '1',
    profitCurrency: 'quote', orderFixedIn: 'quote',
    indicators: [{ uuid: uuidv4(), type: IndicatorEnum.rsi, indicatorLength: 9, indicatorValue: '30',
      indicatorCondition: IndicatorStartConditionEnum.lt, indicatorInterval: ExchangeIntervals.fiveM,
      indicatorAction: IndicatorAction.startDeal, groupId: gid }],
    indicatorGroups: [{ id: gid, logic: IndicatorsLogicEnum.and, action: IndicatorAction.startDeal, section: IndicatorSection.controller }],
  },
  fullResult: true,
} as any)

bt.test([{ interval: ExchangeIntervals.fiveM, bar: data5m }] as any).then((r: any) => {
  const d = r.deals[0]
  console.log(`\n── Open deal ──`)
  console.log(`Start time: ${new Date(d.startTime).toISOString()}`)
  console.log(`Start price: ${d.startPrice}`)
  console.log(`Avg price: ${d.avgPrice}`)
  console.log(`Status: ${d.status}`)
  console.log(`\nFilled orders (${d.filledOrders.length}):`)
  for (const o of d.filledOrders) {
    console.log(`  ${o.side} ${o.type} ${o.qty?.toFixed(6)} @ $${o.price} filled ${new Date(o.filledTime).toISOString()}`)
  }
  console.log(`\nActive orders (${d.activeOrders?.length ?? 0}):`)
  for (const o of (d.activeOrders ?? [])) {
    console.log(`  ${o.side} ${o.type} ${o.qty?.toFixed(6)} @ $${o.price}`)
  }
  // Compute expected TP price for L1
  const l1Price = d.filledOrders[0].price
  const expectedTpL1 = l1Price * 1.004
  console.log(`\nExpected TP for L1 only: $${l1Price} * 1.004 = $${expectedTpL1.toFixed(2)}`)
  // After DCA — avg between L1 and L2
  if (d.filledOrders.length >= 2) {
    const l2Price = d.filledOrders[1].price
    const avg = (l1Price + l2Price) / 2  // assuming equal-size DCA
    console.log(`Avg after L2: ($${l1Price} + $${l2Price}) / 2 = $${avg.toFixed(2)}`)
    console.log(`Expected TP after L2: $${(avg * 1.004).toFixed(2)}`)
  }
  // Highest price BTC reached after L1
  const afterEntry = data5m.filter((b: any) => b.time > d.startTime)
  const maxHigh = Math.max(...afterEntry.map((b: any) => b.high))
  console.log(`\nBTC max high after entry: $${maxHigh.toFixed(2)}`)
  console.log(`Would TP have hit? ${maxHigh > expectedTpL1 ? 'YES (should have closed)' : 'NO'}`)
})
