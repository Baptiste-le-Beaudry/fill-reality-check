"""Public-data example: random entries on real Binance spot bars, research-style exits.

    python examples/make_public_sample.py            # downloads ~4 small monthly archives
    fill-reality-check examples/public_sample/trades.csv --rules examples/public_sample/rules.toml \
        --out examples/public_sample/report --n-trials 20

Entry minutes are drawn at random (seeded) in Jan-Feb 2024 on BTCUSDT and ETHUSDT, long
only. Exits use a generic 0.5 % stop, a 0.3 % / 0.3 % trailing stop and a 60-minute cap,
computed with the common research shortcut (levels checked on closes, filled at the level),
which is what the report is meant to catch. This is not a strategy. Honesty note: three
generic exit rules were tried on these random entries and this one was kept because it
shows the gap between conventions most clearly; its numbers are an illustration only.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from fill_reality_check.bars import BinanceArchive
from fill_reality_check.fills import TradeRule, simulate
from fill_reality_check.synthetic import spaced_random_minutes

HERE = Path(__file__).resolve().parent / "public_sample"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
START, END = pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-02-29 23:59", tz="UTC")
RULE = TradeRule(max_bars=60, stop=0.005, trail_activation=0.003, trail_distance=0.003)
N_PER_SYMBOL = 120

RULES = """# Declared assumptions of the sample backtest (generic, not a strategy)
[costs]
fee_per_side_pct = 0.075   # e.g. a discounted spot taker fee

[exit]
stop_pct = 0.5
trail_activation_pct = 0.3
trail_distance_pct = 0.3
max_bars = 60

[data]
market = "spot"
"""


def main(cache_dir=".frc_cache"):
    rng = np.random.default_rng(2024)
    src = BinanceArchive("spot", cache_dir)
    rows = []
    for sym in SYMBOLS:
        b = src.load(sym, START, END)
        o, h, l, c = (b[k].to_numpy() for k in ("open", "high", "low", "close"))
        picks = spaced_random_minutes(rng, 5, len(c) - 70, N_PER_SYMBOL, 61)
        for s in picks:
            sim = simulate(o, h, l, c, s, RULE, 1, "signal_close", "research")
            rows.append({"symbol": sym, "side": "long", "entry_time": b.index[s].strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "entry_price": sim["entry_price"],
                         "exit_time": b.index[sim["exit_idx"]].strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "exit_price": round(sim["exit_price"], 8), "exit_reason": sim["exit_reason"]})
    df = pd.DataFrame(rows).sort_values(["entry_time", "symbol"]).reset_index(drop=True)
    df.insert(0, "trade_id", np.arange(1, len(df) + 1))
    HERE.mkdir(parents=True, exist_ok=True)
    df.to_csv(HERE / "trades.csv", index=False)
    (HERE / "rules.toml").write_text(RULES, encoding="utf-8")
    print("wrote", HERE, len(df), "trades")


if __name__ == "__main__":
    main()
