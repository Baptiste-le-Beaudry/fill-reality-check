"""Synthetic bars and a "trapped" backtest with known errors (for tests and the offline example).

Nothing here is a strategy: entries are random minutes, and the exits are computed
with the common research shortcut (stops checked on closes, filled at the level) so
that the report has something to find.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .fills import TradeRule, simulate


def random_walk_bars(n: int = 20_000, start: str = "2024-01-01", seed: int = 1, price: float = 100.0,
                     vol: float = 0.0012, gap_prob: float = 0.002) -> pd.DataFrame:
    """1-minute OHLC random walk with occasional opening gaps (fat tails)."""
    rng = np.random.default_rng(seed)
    r = rng.standard_t(4, n) * vol / np.sqrt(2)
    gaps = rng.random(n) < gap_prob
    r[gaps] += rng.normal(0, 8 * vol, gaps.sum())
    close = price * np.exp(np.cumsum(r))
    open_ = np.concatenate([[price], close[:-1]])
    open_[gaps] = open_[gaps] * np.exp(rng.normal(0, 6 * vol, gaps.sum()))  # open away from the previous close
    wick = np.abs(rng.normal(0, vol, (2, n))) * np.maximum(open_, close)
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    idx = pd.date_range(start, periods=n, freq="min", tz="UTC", name="open_time")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def spaced_random_minutes(rng, lo: int, hi: int, k: int, spacing: int) -> list[int]:
    """Up to ``k`` random bar indices in [lo, hi), at least ``spacing`` bars apart, spread
    over the whole range (no early-period bias)."""
    picked: list[int] = []
    for s in rng.permutation(np.arange(lo, hi)):
        if len(picked) == k:
            break
        if all(abs(int(s) - q) > spacing for q in picked):
            picked.append(int(s))
    return sorted(picked)


def trapped_trades(bars_by_symbol: dict[str, pd.DataFrame], n_per_symbol: int = 150, seed: int = 2,
                   stop: float = 0.006, trail=(0.004, 0.005), max_bars: int = 45,
                   n_outside: int = 3, n_lookahead: int = 3, n_overlap: int = 4) -> pd.DataFrame:
    """Trades with research-convention exits plus a few planted errors:
    entries outside the bar range, entries before the signal candle closed, overlaps."""
    rng = np.random.default_rng(seed)
    rule = TradeRule(max_bars=max_bars, stop=stop, trail_activation=trail[0], trail_distance=trail[1])
    rows = []
    for sym, b in bars_by_symbol.items():
        o, h, l, c = (b[k].to_numpy() for k in ("open", "high", "low", "close"))
        n = len(c)
        picked = spaced_random_minutes(rng, 10, n - max_bars - 5, n_per_symbol, max_bars + 1)  # one at a time ...
        for s in picked[:n_overlap]:  # ... except a few planted overlaps
            picked.append(s + 3)
        for s in sorted(picked):
            side = 1 if rng.random() < 0.7 else -1
            sim = simulate(o, h, l, c, int(s), rule, side, "signal_close", "research")
            rows.append({"symbol": sym, "side": "long" if side == 1 else "short",
                         "signal_time": b.index[s], "entry_time": b.index[s], "entry_price": sim["entry_price"],
                         "exit_time": b.index[sim["exit_idx"]], "exit_price": sim["exit_price"],
                         "exit_reason": sim["exit_reason"],
                         "stop_price": sim["entry_price"] * (1 - side * stop)})
    df = pd.DataFrame(rows).sort_values(["entry_time", "symbol"]).reset_index(drop=True)
    df.insert(0, "trade_id", np.arange(1, len(df) + 1))
    # planted: entry price 0.5 % above the bar high
    for i in rng.choice(len(df), n_outside, replace=False):
        b = bars_by_symbol[df.at[i, "symbol"]]
        df.at[i, "entry_price"] = float(b.loc[df.at[i, "entry_time"], "high"]) * 1.005
    # planted: entered one minute BEFORE the signal bar closed (entry_time = signal_time)
    # while the declared signal is one bar later
    for i in rng.choice(len(df), n_lookahead, replace=False):
        df.at[i, "signal_time"] = df.at[i, "entry_time"] + pd.Timedelta(minutes=1)
    df["signal_time"] = df.signal_time.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    df["entry_time"] = df.entry_time.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    df["exit_time"] = df.exit_time.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return df
