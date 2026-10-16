"""Offline example: synthetic bars + a "trapped" backtest with known errors.

    python examples/make_synthetic.py
    fill-reality-check examples/synthetic/trades.csv --rules examples/synthetic/rules.toml \
        --bars-dir examples/synthetic/bars --out examples/synthetic/report --n-trials 40

The bars are a random walk with fat tails and occasional gaps; symbols are fake
(SYNAUSDT, SYNBUSDT). Entries are random minutes. Nothing here is a strategy.
"""
from pathlib import Path

from fill_reality_check.synthetic import random_walk_bars, trapped_trades

HERE = Path(__file__).resolve().parent / "synthetic"

RULES = """# Declared assumptions of the (synthetic) backtest
[costs]
fee_per_side_pct = 0.05

[exit]
trail_activation_pct = 0.4
trail_distance_pct = 0.5
max_bars = 45

[checks]
one_position_per_symbol = true
"""


def main():
    (HERE / "bars").mkdir(parents=True, exist_ok=True)
    bars = {"SYNAUSDT": random_walk_bars(43_200, seed=21, price=100.0),
            "SYNBUSDT": random_walk_bars(43_200, seed=22, price=2.5, vol=0.0015)}
    for sym, b in bars.items():
        b.reset_index().to_csv(HERE / "bars" / f"{sym}.csv", index=False, float_format="%.6f")
    trapped_trades(bars, n_per_symbol=150, seed=23).to_csv(HERE / "trades.csv", index=False, float_format="%.6f")
    (HERE / "rules.toml").write_text(RULES, encoding="utf-8")
    print("wrote", HERE)


if __name__ == "__main__":
    main()
