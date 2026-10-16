# Sample report: random entries on BTCUSDT/ETHUSDT (public data)

*fill-reality-check 0.1.0 - 240 trades read, 240 analysed - period 2024-01-01 -> 2024-02-29 - Binance spot (data.binance.vision) 1-minute bars - round-trip cost 0.150 %*

## Bottom line

On these 240 trades, the profit factor goes from 0.411 as declared to 0.337 when entries fill at the next bar's open and stops are resting orders (round-trip cost 0.150 %).

## Same trades, different fill conventions

Declared exit rule: stop 0.5 %; trailing 0.3 % activation / 0.3 % distance; time exit after 60 bars.

| Simulation | Trades | PF | Mean gross | Mean net | Win % |
|---|---|---|---|---|---|
| Declared (prices in your CSV) | 240 | 0.411 | +0.018 % | -0.132 % | 33.3 |
| Re-simulated: stops checked on closes, filled at the stop level | 240 | 0.411 | +0.018 % | -0.132 % | 33.3 |
| Exit at the close that breached the level | 240 | 0.351 | -0.007 % | -0.157 % | 30.4 |
| Resting stop intrabar (gap fills at the open) | 240 | 0.337 | +0.002 % | -0.148 % | 31.7 |
| Executable: next-bar open entry + resting stops | 240 | 0.337 | +0.002 % | -0.148 % | 31.7 |
| Executable + 1 bar(s) latency | 240 | 0.297 | -0.017 % | -0.167 % | 27.1 |
| Executable + 2 bar(s) latency | 240 | 0.300 | -0.014 % | -0.164 % | 31.7 |
| Executable + 5 bar(s) latency | 240 | 0.360 | +0.002 % | -0.148 % | 31.7 |

## Certain errors (provable from your data and public bars)

| Code | Finding | Trades | Evidence | Impact |
|---|---|---|---|---|
| C5 | Stops checked on closes but filled at the stop level | 66 | Re-simulating your declared rule with stops checked on 1-minute closes and filled at the level reproduces 100.0 % of your declared returns to 1e-6. (Exit at the close: 61.3 %; resting stop: 72.9 %.) That convention cannot be executed: a resting stop fires on intrabar wicks, and a rule that only looks at closes can only exit at the close. | PF 0.411 -> 0.351 (exit at the close) -> 0.337 (resting stop). Mean net per trade -0.132 % -> -0.157 % -> -0.148 %. 66 trades change outcome. |
| C6 | Stop level traded through before the declared exit | 24 | The bar lows (highs for shorts) reached the declared stop level at least one minute before the declared exit, yet the position stayed open. | If your stop is a resting order, these trades should have been stopped earlier. |

## Look-ahead hints (facts that are errors if your signal uses that bar)

| Code | Finding | Trades | Evidence | Impact |
|---|---|---|---|---|
| H1 | Entries stamped at a bar's open time but filled at its close | 236 | The entry price equals the close of the bar whose open time is the entry time. If the signal uses that close, the backtest enters at the very price that created the signal. | Entering at the next bar's open instead: PF 0.337 -> 0.337, mean net -0.148 % -> -0.148 %. |

## Estimated risks: fees and positions

| Code | Finding | Trades | Evidence | Impact |
|---|---|---|---|---|
| R1 | Declared fees below the public base-tier taker fee |  | Declared 0.075 % per side; Binance spot base tier (hard-coded, verify the current schedule): maker 0.100 %, taker 0.100 %. | Market and stop orders pay taker fees unless you have a discount. Your figure is even below the maker fee. |

## Estimated risks: statistics

| Check | Result |
|---|---|
| Cost break-even | Executable mean gross +0.002 % per trade: the mean trade breaks even at a round-trip cost of 0.002 % (you declared 0.150 %). |
| Block bootstrap (declared) | 2000 resamples of 5-day blocks over 60 days: sum of net returns p5 -43.1 %, median -31.7 %, p95 -20.0 %; P(sum <= 0) = 1.00; max drawdown median 32.4 %, p95 43.6 % (additive, one unit of notional per trade). |
| Block bootstrap (executable) | 2000 resamples of 5-day blocks over 60 days: sum of net returns p5 -45.3 %, median -35.6 %, p95 -26.1 %; P(sum <= 0) = 1.00; max drawdown median 36.0 %, p95 45.6 % (additive, one unit of notional per trade). |
| Sharpe statistics (declared) | Per-trade Sharpe -0.3281 over 240 trades (skew 1.26, kurtosis 7.1); PSR(0) = 0.000. With 20 trials, expected best-of-noise Sharpe 0.1229; Deflated Sharpe Ratio = 0.000 (trial Sharpe variance assumed = 1/(T-1), i.e. trials differ only by noise: this is an UPPER bound; real variant sets usually disperse more, which lowers the DSR. Pass trials_sr_variance if you measured it). |
| Sharpe statistics (executable) | Per-trade Sharpe -0.4153 over 240 trades (skew 0.49, kurtosis 4.0); PSR(0) = 0.000. With 20 trials, expected best-of-noise Sharpe 0.1229; Deflated Sharpe Ratio = 0.000 (trial Sharpe variance assumed = 1/(T-1), i.e. trials differ only by noise: this is an UPPER bound; real variant sets usually disperse more, which lowers the DSR. Pass trials_sr_variance if you measured it). |
| Concentration (declared) | Sum of net returns -31.6 %; without the best 2 trades (1 %): -35.1 %; without the best 12 (5 %): -42.8 %; without the 5 best days: -37.7 %. |
| Concentration (executable) | Sum of net returns -35.6 %; without the best 2 trades (1 %): -37.9 %; without the best 12 (5 %): -43.8 %; without the 5 best days: -39.5 %. |

## Cost and latency sensitivity (executable fills)

Cells: PF / mean net per trade. Rows: extra cost per side on top of the declared cost.

| Extra cost / side | latency 0 bar(s) | latency 1 bar(s) | latency 2 bar(s) | latency 5 bar(s) |
|---|---|---|---|---|
| +0.00 % | 0.34 / -0.148 % | 0.30 / -0.167 % | 0.30 / -0.164 % | 0.36 / -0.148 % |
| +0.01 % | 0.29 / -0.168 % | 0.26 / -0.187 % | 0.26 / -0.184 % | 0.31 / -0.168 % |
| +0.02 % | 0.25 / -0.188 % | 0.23 / -0.207 % | 0.22 / -0.204 % | 0.28 / -0.188 % |
| +0.05 % | 0.17 / -0.248 % | 0.15 / -0.267 % | 0.15 / -0.264 % | 0.18 / -0.248 % |
| +0.10 % | 0.09 / -0.348 % | 0.08 / -0.367 % | 0.07 / -0.364 % | 0.10 / -0.348 % |
| +0.20 % | 0.03 / -0.548 % | 0.02 / -0.567 % | 0.02 / -0.564 % | 0.03 / -0.548 % |

## By year of entry (mean net per trade)

| Year | Trades | Declared | Close-checked at level | Resting stop | Executable |
|---|---|---|---|---|---|
| 2024 | 240 | -0.132 % | -0.132 % | -0.148 % | -0.148 % |

## Not checked

- order book depth and market impact (fills assume your size trades at the bar prices)
- funding payments on perpetual futures
- exchange outages, rejected orders, partial fills
- anything that happens inside a 1-minute bar beyond its open/high/low/close
- your signal logic itself (only the fills and the statistics are checked)

## Method

Bars are labelled by their open time. 'Executable' enters at the open of the bar after the signal bar (plus latency), tests stops and trailing stops as resting orders on each bar's low/high, fills a gap through the level at the open, arms the trailing level from the peak observed up to the previous bar, fills a take-profit only if the bar trades through it (never better than the target), lets the stop win when both are hit in one bar, and exits on time at the bar's close. Stop and target distances are kept from your declared entry price. Costs are subtracted per trade as a round-trip fraction.

Flagged trades are listed in `flagged_trades.csv`.

*This report is a technical review of a trade simulation. It is not investment advice, not a recommendation to trade any asset and not a forecast of returns. Simulated or past results do not guarantee future results.*
