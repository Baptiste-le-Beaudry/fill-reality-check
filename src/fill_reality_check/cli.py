"""Command line: ``fill-reality-check trades.csv [--rules rules.toml] [--out report_dir]``."""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from .analyze import analyze
from .bars import BinanceArchive, LocalBars
from .inputs import Rules, read_trades
from .report import write_reports


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fill-reality-check",
                                 description="Re-simulate a crypto trade ledger on public 1-minute Binance bars.")
    ap.add_argument("trades", help="trades CSV (see README for the columns)")
    ap.add_argument("--rules", help="rules TOML file (declared assumptions)")
    ap.add_argument("--out", default="frc_report", help="output directory (default: frc_report)")
    ap.add_argument("--title", default="Fill-Reality Check report")
    ap.add_argument("--fee-per-side-pct", type=float, help="override the declared fee per side, in percent")
    ap.add_argument("--n-trials", type=int, help="number of variants tried (enables the Deflated Sharpe Ratio)")
    ap.add_argument("--trials-matrix", help="CSV, one column of per-period returns per variant (enables PBO)")
    ap.add_argument("--market", choices=("spot", "um"), help="Binance market for the bars (default from rules: spot)")
    ap.add_argument("--cache-dir", default=".frc_cache", help="bar cache directory (default: .frc_cache)")
    ap.add_argument("--offline", action="store_true", help="never download; use the cache only")
    ap.add_argument("--bars-dir", help="use local bar files instead of Binance")
    ap.add_argument("--bars-pattern", default="{symbol}.csv", help="file name pattern in --bars-dir")
    ap.add_argument("--allow-pickle", action="store_true", help="allow .pkl bar files (only files you created)")
    ap.add_argument("-q", "--quiet", action="store_true")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    rules = Rules.from_file(a.rules, fee_per_side_pct=a.fee_per_side_pct, n_trials=a.n_trials, market=a.market)
    trades, problems = read_trades(a.trades)
    if a.bars_dir:
        loader = LocalBars(a.bars_dir, a.bars_pattern, a.allow_pickle)
    else:
        loader = BinanceArchive(rules.market, a.cache_dir, offline=a.offline, verbose=not a.quiet)
    tm = pd.read_csv(a.trials_matrix, index_col=0) if a.trials_matrix else None
    res = analyze(trades, problems, rules, loader, tm, verbose=not a.quiet)
    res["bars_source"] = "local" if a.bars_dir else f"Binance {rules.market} (data.binance.vision)"
    paths = write_reports(res, a.out, a.title)
    if not a.quiet:
        for w in res["waterfall"]:
            print(f"  {w['label'][:62]:62s} n={w['n']:5d}  PF={w['pf']:.3f}  net/trade={w['mean_net_pct']:+.3f} %")
        print("wrote", ", ".join(str(p) for p in paths.values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
