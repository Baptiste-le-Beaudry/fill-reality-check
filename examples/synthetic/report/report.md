# Sample report: synthetic trapped backtest

*fill-reality-check 0.1.0 - 308 trades read, 308 analysed - period 2024-01-01 -> 2024-01-30 - local 1-minute bars - round-trip cost 0.100 %*

## Bottom line

On these 308 trades, the profit factor goes from 0.918 as declared to 0.333 when entries fill at the next bar's open and stops are resting orders (round-trip cost 0.100 %).

## Same trades, different fill conventions

Declared exit rule: per-trade stop from the CSV; trailing 0.4 % activation / 0.5 % distance; time exit after 45 bars.

| Simulation | Trades | PF | Mean gross | Mean net | Win % |
|---|---|---|---|---|---|
| Declared (prices in your CSV) | 308 | 0.918 | +0.074 % | -0.026 % | 44.5 |
| Re-simulated: stops checked on closes, filled at the stop level | 308 | 0.936 | +0.080 % | -0.020 % | 45.1 |
| Exit at the close that breached the level | 308 | 0.742 | +0.003 % | -0.097 % | 42.2 |
| Resting stop intrabar (gap fills at the open) | 308 | 0.333 | -0.108 % | -0.208 % | 27.3 |
| Executable: next-bar open entry + resting stops | 308 | 0.333 | -0.108 % | -0.208 % | 27.3 |
| Executable + 1 bar(s) latency | 308 | 0.374 | -0.076 % | -0.176 % | 27.3 |
| Executable + 2 bar(s) latency | 308 | 0.363 | -0.096 % | -0.196 % | 29.5 |
| Executable + 5 bar(s) latency | 308 | 0.407 | -0.078 % | -0.178 % | 31.8 |

## Certain errors (provable from your data and public bars)

| Code | Finding | Trades | Evidence | Impact |
|---|---|---|---|---|
| C1 | Entry price outside the bar's high/low range | 3 | The declared entry price is not inside [low, high] of the 1-minute bar at the entry time. | Such fills cannot have happened at that minute (wrong timestamp, wrong timezone, or a price taken from another bar). |
| C2 | Exit price outside the bar's high/low range | 1 | The declared exit price is not inside [low, high] of the 1-minute bar at the exit time. | Such fills cannot have happened at that minute. |
| C3 | Entry before the signal candle closed (look-ahead) | 6 | entry_time is earlier than signal_time + 1 minute and the entry price is not the signal bar's close: the order was filled before the bar that produced the signal had closed. | The backtest used information it did not have yet. |
| C4 | Stop exits filled better than the bar's open | 1 | Exit reason is a stop, but the exit bar opened beyond the declared fill: a resting stop would have filled at that open (gap) or earlier. | Losses on gaps are understated. |
| C5 | Stops checked on closes but filled at the stop level | 207 | Re-simulating your declared rule with stops checked on 1-minute closes and filled at the level reproduces 98.4 % of your declared returns to 1e-6. (Exit at the close: 30.8 %; resting stop: 32.8 %.) That convention cannot be executed: a resting stop fires on intrabar wicks, and a rule that only looks at closes can only exit at the close. | PF 0.936 -> 0.742 (exit at the close) -> 0.333 (resting stop). Mean net per trade -0.020 % -> -0.097 % -> -0.208 %. 207 trades change outcome. |
| C6 | Stop level traded through before the declared exit | 123 | The bar lows (highs for shorts) reached the declared stop level at least one minute before the declared exit, yet the position stayed open. | If your stop is a resting order, these trades should have been stopped earlier. |
| C7 | Overlapping positions on the same symbol | 8 | Trades open while an earlier trade on the same symbol is still open; up to 4 positions open at once across all symbols. Your rules say one position per symbol. | Keeping only the first of overlapping trades (300 trades): declared PF 0.912, executable PF 0.331. |

## Look-ahead hints (facts that are errors if your signal uses that bar)

| Code | Finding | Trades | Evidence | Impact |
|---|---|---|---|---|
| H1 | Entries stamped at a bar's open time but filled at its close | 305 | The entry price equals the close of the bar whose open time is the entry time. If the signal uses that close, the backtest enters at the very price that created the signal. | Entering at the next bar's open instead: PF 0.333 -> 0.333, mean net -0.208 % -> -0.208 %. |
| H2 | Entries filled at the signal bar's close | 302 | The entry price equals the close of the signal bar. | Not executable if the signal needs that close. |

## Estimated risks: fees and positions

| Code | Finding | Trades | Evidence | Impact |
|---|---|---|---|---|
| R1 | Declared fees below the public base-tier taker fee |  | Declared 0.050 % per side; Binance spot base tier (hard-coded, verify the current schedule): maker 0.100 %, taker 0.100 %. | Market and stop orders pay taker fees unless you have a discount. Your figure is even below the maker fee. |

## Estimated risks: statistics

| Check | Result |
|---|---|
| Cost break-even | Executable mean gross -0.108 % per trade: negative before any cost, no cost level breaks even. |
| Block bootstrap (declared) | 2000 resamples of 5-day blocks over 30 days: sum of net returns p5 -28.5 %, median -9.1 %, p95 13.0 %; P(sum <= 0) = 0.75; max drawdown median 16.7 %, p95 31.2 % (additive, one unit of notional per trade). |
| Block bootstrap (executable) | 2000 resamples of 5-day blocks over 30 days: sum of net returns p5 -74.5 %, median -64.4 %, p95 -53.5 %; P(sum <= 0) = 1.00; max drawdown median 64.5 %, p95 74.6 % (additive, one unit of notional per trade). |
| Sharpe statistics (declared) | Per-trade Sharpe -0.0351 over 308 trades (skew 1.08, kurtosis 3.9); PSR(0) = 0.273. With 40 trials, expected best-of-noise Sharpe 0.1250; Deflated Sharpe Ratio = 0.003 (trial Sharpe variance assumed = 1/(T-1), i.e. trials differ only by noise: this is an UPPER bound; real variant sets usually disperse more, which lowers the DSR. Pass trials_sr_variance if you measured it). |
| Sharpe statistics (executable) | Per-trade Sharpe -0.4202 over 308 trades (skew 1.29, kurtosis 6.6); PSR(0) = 0.000. With 40 trials, expected best-of-noise Sharpe 0.1250; Deflated Sharpe Ratio = 0.000 (trial Sharpe variance assumed = 1/(T-1), i.e. trials differ only by noise: this is an UPPER bound; real variant sets usually disperse more, which lowers the DSR. Pass trials_sr_variance if you measured it). |
| Concentration (declared) | Sum of net returns -8.1 %; without the best 3 trades (1 %): -15.9 %; without the best 15 (5 %): -36.9 %; without the 5 best days: -23.5 %. |
| Concentration (executable) | Sum of net returns -63.9 %; without the best 3 trades (1 %): -69.9 %; without the best 15 (5 %): -80.7 %; without the 5 best days: -65.8 %. |

## Cost and latency sensitivity (executable fills)

Cells: PF / mean net per trade. Rows: extra cost per side on top of the declared cost.

| Extra cost / side | latency 0 bar(s) | latency 1 bar(s) | latency 2 bar(s) | latency 5 bar(s) |
|---|---|---|---|---|
| +0.00 % | 0.33 / -0.208 % | 0.37 / -0.176 % | 0.36 / -0.196 % | 0.41 / -0.178 % |
| +0.01 % | 0.30 / -0.228 % | 0.34 / -0.196 % | 0.33 / -0.216 % | 0.37 / -0.198 % |
| +0.02 % | 0.27 / -0.248 % | 0.30 / -0.216 % | 0.30 / -0.236 % | 0.34 / -0.218 % |
| +0.05 % | 0.21 / -0.308 % | 0.23 / -0.276 % | 0.22 / -0.296 % | 0.26 / -0.278 % |
| +0.10 % | 0.13 / -0.408 % | 0.14 / -0.376 % | 0.14 / -0.396 % | 0.17 / -0.378 % |
| +0.20 % | 0.06 / -0.608 % | 0.06 / -0.576 % | 0.06 / -0.596 % | 0.08 / -0.578 % |

## By year of entry (mean net per trade)

| Year | Trades | Declared | Close-checked at level | Resting stop | Executable |
|---|---|---|---|---|---|
| 2024 | 308 | -0.026 % | -0.020 % | -0.208 % | -0.208 % |

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
