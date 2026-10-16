# Examples

Two generated reports. Neither one contains a trading strategy.

| Folder | Data | What it shows |
|---|---|---|
| [synthetic/](synthetic/) | fake symbols (`SYNAUSDT`, `SYNBUSDT`), random-walk bars with fat tails and gaps | a "trapped" backtest with planted errors: entries outside the bar range, entries before the signal candle closed, overlapping positions, exits checked on closes and filled at the level |
| [public_sample/](public_sample/) | public Binance spot bars, BTCUSDT and ETHUSDT, Jan-Feb 2024 | 240 random long entries with a generic 0.5 % stop, a 0.3 % / 0.3 % trailing stop and a 60-minute cap, exits computed with the close-checked shortcut |

Each folder has `trades.csv`, `rules.toml` and `report/` (`report.md`, `report.html`,
`flagged_trades.csv`, `summary.json`).

Regenerate:

```bash
python examples/make_synthetic.py
fill-reality-check examples/synthetic/trades.csv --rules examples/synthetic/rules.toml \
    --bars-dir examples/synthetic/bars --out examples/synthetic/report --n-trials 40 \
    --title "Sample report: synthetic trapped backtest"

python examples/make_public_sample.py      # downloads 4 monthly archives (~10 MB) into .frc_cache/
fill-reality-check examples/public_sample/trades.csv --rules examples/public_sample/rules.toml \
    --out examples/public_sample/report --n-trials 20 \
    --title "Sample report: random entries on BTCUSDT/ETHUSDT (public data)"
```

Random entries lose money after fees, so both samples are negative overall. They are
there to show the gap between fill conventions, not a result. For the public sample,
three generic exit rules were tried, and the one that shows the gap most clearly was
kept.
