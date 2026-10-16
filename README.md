# fill-reality-check

*[Version française : README.fr.md](README.fr.md)*

Your backtest says profit factor 1.4. Could those fills actually have happened?

`fill-reality-check` is a small local command-line tool. You give it a CSV of crypto
trades. It re-simulates every trade on public 1-minute Binance bars and writes a
robustness report in Markdown and HTML. The report keeps **certain errors** (provable
from your data and the exchange's bars) apart from **estimated risks** (sensitivities
and statistics that depend on assumptions).

It was built after an audit of a real bot: 2,549 trades with a profit factor of 1.38
became 0.95 once the stops were filled the way a real order would fill them. See
[docs/case_study.md](docs/case_study.md).

## What it checks

**Certain errors**

| Code | Check |
|---|---|
| C1 / C2 | Entry or exit price outside the high/low range of the 1-minute bar at that time |
| C3 | Entry stamped before the signal candle closed (look-ahead) |
| C4 | Stop exit filled better than the open of a bar that gapped through it |
| C5 | Stops checked on closes but filled at the stop level: the tool finds which convention reproduces your returns and re-simulates the same trades three ways (close-checked at the level / exit at the close / resting stop intrabar) |
| C6 | Stop level traded through intrabar before the declared exit |
| C7 | Overlapping positions on the same symbol (when you declare one position per symbol) |
| C8 / C9 | Input problems (duplicates, exit before entry, unknown side) and trades that cannot be located on the bars |

**Look-ahead hints** (errors if your signal uses that bar's close)

| Code | Check |
|---|---|
| H1 | Entry stamped at a bar's open time but filled at that bar's close |
| H2 | Entry filled at the signal bar's close |
| H3 | Missing minutes inside the trade window (signals across data gaps are often artefacts) |

**Estimated risks**

- Cost and latency sensitivity: PF and net per trade for extra cost per side
  (0 to 0.20 %) x latency (0, 1, 2, 5 bars), and the break-even round-trip cost.
- Block bootstrap of the daily net series (blocks of days, not trades).
- Probabilistic Sharpe Ratio; Deflated Sharpe Ratio when you give the number of
  variants you tried (`--n-trials`); PBO (CSCV) when you give a matrix of variant returns
  (`--trials-matrix`).
- Concentration (result without the best 1 % / 5 % of trades and the best 5 days),
  results by year, and declared fees compared with Binance's base-tier fee.

## Install

Python 3.11 or newer. Dependencies: `numpy` and `pandas` only.

```bash
git clone <this repository> fill-reality-check
cd fill-reality-check
python -m pip install -e .            # or: pip install -e ".[test]" to run the tests
```

## Use

```bash
fill-reality-check trades.csv --rules rules.toml --out my_report --n-trials 120
```

This writes `my_report/report.md`, `my_report/report.html` (self-contained, no external
assets), `my_report/flagged_trades.csv` (one row per flagged trade) and
`my_report/summary.json`.

Bars are downloaded from [data.binance.vision](https://data.binance.vision) (monthly
archives, checked against the published SHA-256 checksum, then daily archives and the
public REST endpoint for the latest days). They are cached in `.frc_cache/` (git-ignored).
No API key is used or needed. `--offline` uses the cache only. `--market um` uses USD-M
perpetual futures bars instead of spot. `--bars-dir DIR --bars-pattern "{symbol}.csv"`
uses your own bar files instead.

Offline example, no download needed:

```bash
python examples/make_synthetic.py
fill-reality-check examples/synthetic/trades.csv --rules examples/synthetic/rules.toml \
    --bars-dir examples/synthetic/bars --out examples/synthetic/report --n-trials 40
```

Generated sample reports are in [examples/](examples/).

## Input: `trades.csv`

One row per trade. Times are UTC (ISO 8601; a `Z` or `+00:00` suffix is fine).

| Column | Required | Example | Notes |
|---|---|---|---|
| `symbol` | yes | `SOLUSDT` | Binance symbol (`SOL/USDT` is accepted) |
| `side` | yes | `long` / `short` | also `buy` / `sell` |
| `entry_time` | yes | `2024-03-02T14:32:00Z` | |
| `entry_price` | yes | `128.41` | |
| `exit_time` | yes | `2024-03-02T15:21:00Z` | |
| `exit_price` | yes | `129.02` | |
| `trade_id` | no | `17` | generated if missing |
| `signal_time` | recommended | `2024-03-02T14:31:00Z` | open time of the signal bar |
| `exit_reason` | recommended | `stop` / `trail` / `target` / `time` | |
| `stop_price` or `stop_distance` | no | `126.10` or `0.018` | per-trade stop (distance as a fraction) |
| `target_price` or `target_distance` | no | `131.00` | per-trade take-profit |
| `fee_per_side_pct` | no | `0.05` | overrides the rules file for that trade |

Without `signal_time`, the signal bar is inferred: if the entry price is closer to the
close of the bar stamped at `entry_time` than to its open, that bar is the signal bar
(the trade was filled at its close); otherwise the previous bar is.

## Rules: `rules.toml`

The assumptions your backtest made. Sections are optional; keys are what matter.

```toml
[costs]
fee_per_side_pct = 0.07        # what the backtest assumed, percent per side
slippage_per_side_pct = 0.0

[exit]                         # re-simulation rule; per-trade CSV stops override stop_pct
stop_pct = 1.5
target_pct = 3.0
trail_activation_pct = 0.6     # trailing arms once the best excursion reaches this
trail_distance_pct = 0.8
max_bars = 50                  # or "declared" (default): each trade's own holding time

[checks]
one_position_per_symbol = true
price_tolerance_pct = 0.0      # slack for the high/low range checks

[stats]
n_trials = 120                 # variants you tried (for the Deflated Sharpe Ratio)
trials_sr_variance = 0.0004    # optional: measured variance of the variants' per-trade Sharpe ratios
bootstrap_block_days = 5
bootstrap_samples = 2000

[sensitivity]
extra_cost_per_side_pct = [0.0, 0.01, 0.02, 0.05, 0.10, 0.20]
latency_bars = [0, 1, 2, 5]

[data]
market = "spot"                # or "um" (USD-M futures)
```

## Fill conventions

Bars are labelled by their open time. Given the signal bar `s`:

- **Close-checked, filled at the level** (the research shortcut): entry at `close[s]`;
  stop, trailing stop and target checked on each 1-minute close; exit recorded at the
  level. A rule that only looks at closes cannot get the level, so this is not executable.
- **Exit at the close**: same checks, exit at that close.
- **Resting stop intrabar**: stops and trailing stops are resting orders tested on each
  bar's low (high for shorts); a gap through the level fills at the open. The trailing
  level on a bar uses the peak observed up to the previous bar. A take-profit fills only
  if the bar trades through it, never better than the target. If a bar reaches both the
  stop and the target, the stop wins.
- **Executable**: the same resting-order exits, with the entry at the open of bar
  `s + 1 + latency`.

Time exits fill at the close of the last bar. Stop and target distances are kept from
your declared entry price. Costs are subtracted per trade as a round-trip fraction. When a
choice was ambiguous, the less favourable option was taken.

## Limits

- 1-minute bars only. Nothing inside a bar beyond its open, high, low and close is known,
  so the intrabar order of the high and the low is not modelled (the stop is assumed to
  come first).
- No order book: fills assume your size trades at the bar prices. No market impact,
  no partial fills, no funding payments, no exchange outages.
- Fees: the base-tier table is hard-coded (spot 0.10 % maker and taker; USD-M 0.02 % maker,
  0.05 % taker). Check the current schedule.
- The DSR without a measured variance of your variants' Sharpe ratios assumes the
  variants differ only by noise. That is an upper bound.
- The tool checks fills and statistics, not your signal logic. "No certain error found"
  does not mean a strategy is profitable.

## Tests

```bash
python -m pytest
```

## Provenance

The fill engine (`fills.py`) is adapted from an internal execution harness (v1.4.0)
and its exit-realism diagnostic. The DSR, PSR and CSCV formulas follow Bailey and
López de Prado (2014) and Bailey, Borwein, López de Prado and Zhu (2017).

## License and disclaimer

MIT, see [LICENSE](LICENSE).

This tool performs a technical review of a trade simulation. It is not investment
advice, not a recommendation to trade any asset and not a forecast of returns. Simulated
or past results do not guarantee future results.
