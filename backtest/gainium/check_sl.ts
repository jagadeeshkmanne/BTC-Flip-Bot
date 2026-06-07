import * as fs from 'fs'
const CSV = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
const lines = fs.readFileSync(CSV, 'utf-8').trim().split('\n')
const bars: any[] = []
for (let i = 1; i < lines.length; i++) {
  const c = lines[i].split(',')
  bars.push({ time: Math.floor(new Date(c[0]).getTime() / 1000) * 1000, open: +c[1], high: +c[2], low: +c[3], close: +c[4] })
}
// Position opened May 14 17:40 @ $80,750 (LONG)
// Avg now $80,750 with 3 fills (so L1, L2, L3 — but ordersCount=2, so 3 fills means L1 + 2 DCAs?)
const startTime = new Date('2026-05-14T17:40:00Z').getTime()
const afterStart = bars.filter(b => b.time >= startTime)
const lowest = afterStart.reduce((min, b) => b.low < min.low ? b : min, afterStart[0])
const lastBar = afterStart[afterStart.length-1]
const entryL1 = 80750 // approx (we don't know exact L1)
const expectedSL_06 = entryL1 * (1 - 0.006)  // SL 0.6% from L1
const expectedSL_2 = entryL1 * (1 - 0.02)    // SL 2.0% (just in case)
console.log(`After ${new Date(startTime).toISOString().slice(0,16)} open:`)
console.log(`  Lowest price: $${lowest.low.toFixed(2)} on ${new Date(lowest.time).toISOString().slice(0,16)}`)
console.log(`  Last price:   $${lastBar.close.toFixed(2)}`)
console.log(`  Position avg: $80,750`)
console.log(`  Expected SL at 0.6% from L1 entry: $${expectedSL_06.toFixed(2)}`)
console.log(`  Price went BELOW SL? ${lowest.low < expectedSL_06 ? 'YES — SL should have fired!' : 'no'}`)
console.log(`  Price below avg by: ${((lowest.low - 80750) / 80750 * 100).toFixed(2)}%`)
console.log(`  Current still below: ${((lastBar.close - 80750) / 80750 * 100).toFixed(2)}%`)
