# Case study: how fill errors turned a profit factor of 1.38 into 0.95

This is the audit that led to `fill-reality-check`. The trades come from one of my own
crypto bots, so I could check every assumption. The entry logic is not disclosed
here, and it does not matter: **every number below uses exactly the same entries.**
Only the way the exits were filled changes.

All figures are **in-sample** backtest results (walk-forward test trades, 2021-11-30 to
2025-04-07). Nothing here was confirmed in live trading. Round-trip cost: 0.14 %.

## The backtest

- Long-only intraday trades on 9 Binance USDT-M pairs: ADA, BNB, BTC, DOGE, ETH, LINK,
  LTC, SOL, XRP (against USDT). 2,549 trades.
- Exits: a protective stop set per trade, a trailing stop (activates at +0.63 %, trails
  by 0.8 %), and a time exit after 50 one-minute bars.
- The research code checked the stop and the trailing stop on each 1-minute **close**
  and recorded the exit **at the stop level**.

That last point looks harmless. It is the whole story.

## Why "checked on the close, filled at the level" is impossible

A rule can watch prices in two ways, and neither one gives that fill:

1. **A resting stop order** sits at the exchange. It fires as soon as a wick touches the
   level, even if the price recovers before the minute closes. If the market gaps through
   the level, you get the next available price (the bar's open), not the level.
2. **A bot that checks closes** only acts after a close is beyond the level. By then the
   price is often well past the stop, so the bot exits at that close, below the level.

The research convention takes the best of both worlds: it ignores the wicks (like the
close-checking bot) but still gets the exact level (like the resting order). No real
order gets that.

## What the tool found

Same 2,549 trades, same entries, same stop distances, re-simulated on 1-minute bars:

| Fill convention | PF | Mean gross / trade | Mean net / trade | Win rate |
|---|---|---|---|---|
| Declared ledger | 1.379 | +0.347 % | +0.207 % | 61.0 % |
| Re-simulated, close-checked, filled at the level | 1.379 | +0.347 % | +0.207 % | 61.0 % |
| Exit at the close that breached the level | **1.048** | +0.171 % | +0.031 % | 55.1 % |
| Resting stop, intrabar, gap fills at the open | **0.951** | +0.115 % | −0.025 % | 56.2 % |
| Same, entry at the next bar's open | 0.943 | +0.110 % | −0.030 % | 56.3 % |
| ... + 1 bar of latency | 0.930 | +0.105 % | −0.035 % | 55.9 % |
| ... + 2 bars of latency | 0.901 | +0.089 % | −0.051 % | 55.3 % |

The second row reproduces the ledger exactly: 100 % of the declared returns are matched
to within 1e-6. That's how the tool shows *which* convention the backtest used, rather
than guessing. **1,813 of the 2,549 trades (71 %) change outcome** when the stops become
resting orders.

Other certain findings on the same ledger:

- **113 trades** had their stop level traded through intrabar at least one minute before
  the recorded exit, yet the position stayed open.
- **201 trades** opened while an earlier trade on the same symbol was still open. The live
  bot holds one position per symbol. Keeping only the first trade of each overlap
  (2,355 trades) leaves the declared PF almost unchanged (1.374), but the executable PF
  drops to 0.925.
- **2,548 entries** were stamped at a bar's open time but priced at that bar's close.
  Moving them to the next bar's open costs about another 0.004 percentage points per trade.

## Estimated risks

- **Cost break-even.** With executable fills, the mean trade makes +0.110 % before costs.
  It breaks even at a round-trip cost of 0.110 %, below the 0.14 % assumed.
- **Concentration.** In the declared ledger, removing the best 5 % of trades (127) turns a
  sum of +528 % into −33 %.
- **Years.** The declared ledger is positive every year. With executable fills, the
  mean net per trade is negative in 2021, 2022, 2023 and 2024, and positive only in
  the first three months of 2025.

| Entry year | Trades | Declared | Resting stop | Executable |
|---|---|---|---|---|
| 2021 | 33 | +0.056 % | −0.262 % | −0.260 % |
| 2022 | 594 | +0.238 % | −0.067 % | −0.069 % |
| 2023 | 410 | +0.181 % | −0.018 % | −0.026 % |
| 2024 | 1,145 | +0.210 % | −0.034 % | −0.035 % |
| 2025 (Q1) | 367 | +0.193 % | +0.083 % | +0.068 % |

- **Block bootstrap** (5-day blocks, 2,000 resamples) of the executable daily series: the
  sum of net returns is ≤ 0 in 72 % of resamples.
- **Selection.** The rule was picked among several hundred variants. A Deflated Sharpe
  Ratio computed with the tool's default assumption (variants differ only by noise) still
  looks fine for the declared ledger. That assumption is only an upper bound. A separate
  validation that measured how far the variants' Sharpe ratios spread gave a DSR of
  about 0.10 to 0.25 on the cleanest period. With executable fills, the per-trade Sharpe
  ratio is negative, so the question is moot.

## What changed nothing

The entries, the symbols, the stop distances, the trailing parameters and the cost. The
"edge" came from how the exits were simulated.

## Reproducing this on your own trades

```bash
fill-reality-check my_trades.csv --rules my_rules.toml --out report --n-trials <variants tried>
```

The ledger of this case study is private and not included in this repository. The
examples in `examples/` show the same effect on synthetic data and on random entries
using public Binance bars.

*Technical review of a simulation. Not investment advice, not a recommendation to trade,
not a forecast.*
