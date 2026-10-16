"""Reading and validating the trades CSV and the rules file (TOML, stdlib only)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

REQUIRED = ("symbol", "side", "entry_time", "entry_price", "exit_time", "exit_price")
OPTIONAL = ("trade_id", "signal_time", "exit_reason", "stop_price", "stop_distance", "target_price",
            "target_distance", "fee_per_side_pct", "qty")
SIDES = {"long": 1, "buy": 1, "1": 1, "+1": 1, "short": -1, "sell": -1, "-1": -1}


@dataclass
class Rules:
    """Declared assumptions. Percent fields are in percent (0.07 = 0.07 %)."""
    fee_per_side_pct: float = 0.10
    slippage_per_side_pct: float = 0.0
    stop_pct: Optional[float] = None
    target_pct: Optional[float] = None
    trail_activation_pct: Optional[float] = None
    trail_distance_pct: Optional[float] = None
    max_bars: object = "declared"          # int, or "declared" = each trade's declared holding time
    market: str = "spot"                   # spot | um (USD-M futures)
    one_position_per_symbol: bool = True
    price_tolerance_pct: float = 0.0       # tolerance for the high/low range checks
    n_trials: Optional[int] = None         # number of variants tried (for the Deflated Sharpe Ratio)
    trials_sr_variance: Optional[float] = None
    bootstrap_block_days: int = 5
    bootstrap_samples: int = 2000
    seed: int = 7
    extra_cost_per_side_pct: list = field(default_factory=lambda: [0.0, 0.01, 0.02, 0.05, 0.10, 0.20])
    latency_bars: list = field(default_factory=lambda: [0, 1, 2, 5])

    @property
    def cost_rt(self) -> float:
        return 2 * (self.fee_per_side_pct + self.slippage_per_side_pct) / 100

    @classmethod
    def from_file(cls, path: str | Path | None, **overrides) -> "Rules":
        data: dict = {}
        if path:
            with open(path, "rb") as f:
                raw = tomllib.load(f)
            for k, v in raw.items():  # sections ([costs], [exit], ...) are only for readability
                if isinstance(v, dict):
                    data.update(v)
                else:
                    data[k] = v
        data.update({k: v for k, v in overrides.items() if v is not None})
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown rules keys: {unknown}")
        r = cls(**data)
        if r.market not in ("spot", "um"):
            raise ValueError("market must be 'spot' or 'um'")
        if not (r.max_bars == "declared" or (isinstance(r.max_bars, int) and r.max_bars >= 1)):
            raise ValueError("max_bars must be a positive integer or 'declared'")
        if (r.trail_activation_pct is None) != (r.trail_distance_pct is None):
            raise ValueError("trail_activation_pct and trail_distance_pct go together")
        return r


def _ts(s: pd.Series) -> pd.Series:
    out = pd.to_datetime(s, utc=True, errors="coerce", format="mixed")
    return out


def read_trades(path_or_frame) -> tuple[pd.DataFrame, list[dict]]:
    """Return (trades, problems). Rows that cannot be used are kept with ``valid=False``
    and a ``problem`` string, so that they are counted in the report."""
    df = path_or_frame.copy() if isinstance(path_or_frame, pd.DataFrame) else pd.read_csv(path_or_frame)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"trades CSV is missing required columns: {missing}")
    if "trade_id" not in df.columns:
        df["trade_id"] = np.arange(1, len(df) + 1)
    for c in OPTIONAL:
        if c not in df.columns:
            df[c] = np.nan
    df["symbol"] = df.symbol.astype(str).str.upper().str.replace("/", "", regex=False).str.strip()
    df["side_int"] = df.side.astype(str).str.lower().str.strip().map(SIDES)
    for c in ("entry_time", "exit_time", "signal_time"):
        df[c] = _ts(df[c]) if df[c].notna().any() else pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
    for c in ("entry_price", "exit_price", "stop_price", "stop_distance", "target_price", "target_distance",
              "fee_per_side_pct", "qty"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    problems = []
    prob = pd.Series("", index=df.index)

    def flag(mask, text):
        nonlocal prob
        prob = prob.where(~(mask & (prob == "")), text)
        if mask.any():
            problems.append({"problem": text, "n": int(mask.sum()),
                             "trade_ids": df.loc[mask, "trade_id"].tolist()})

    flag(df.side_int.isna(), "unknown side")
    flag(df.entry_time.isna() | df.exit_time.isna(), "unparseable time")
    flag(~(df.entry_price > 0) | ~(df.exit_price > 0), "non-positive or missing price")
    flag(df.exit_time <= df.entry_time, "exit not after entry")
    dup = df.duplicated(["symbol", "side_int", "entry_time"], keep="first")
    flag(dup, "duplicate trade (same symbol, side, entry time)")
    if df.trade_id.duplicated().any():
        problems.append({"problem": "duplicate trade_id (renumbered)", "n": int(df.trade_id.duplicated().sum()),
                         "trade_ids": df.loc[df.trade_id.duplicated(), "trade_id"].tolist()})
        df["trade_id"] = np.arange(1, len(df) + 1)
    if not df.entry_time.is_monotonic_increasing:
        problems.append({"problem": "rows not sorted by entry time (sorted for the analysis)", "n": 0,
                         "trade_ids": []})
    df["problem"] = prob
    df["valid"] = prob == ""
    df = df.sort_values(["entry_time", "symbol"], kind="stable").reset_index(drop=True)
    # declared per-trade distances (fractions, relative to the declared entry price)
    side = df.side_int.fillna(1)
    sd = side * (df.entry_price - df.stop_price) / df.entry_price
    df["stop_d"] = df.stop_distance.where(df.stop_distance.notna(), sd)
    td = side * (df.target_price - df.entry_price) / df.entry_price
    df["target_d"] = df.target_distance.where(df.target_distance.notna(), td)
    bad_stop = df.stop_d.notna() & ~(df.stop_d > 0)
    if bad_stop.any():
        problems.append({"problem": "stop on the wrong side of the entry (ignored)", "n": int(bad_stop.sum()),
                         "trade_ids": df.loc[bad_stop, "trade_id"].tolist()})
        df.loc[bad_stop, "stop_d"] = np.nan
    bad_tg = df.target_d.notna() & ~(df.target_d > 0)
    if bad_tg.any():
        problems.append({"problem": "target on the wrong side of the entry (ignored)", "n": int(bad_tg.sum()),
                         "trade_ids": df.loc[bad_tg, "trade_id"].tolist()})
        df.loc[bad_tg, "target_d"] = np.nan
    df["declared_gross"] = side * (df.exit_price / df.entry_price - 1.0)
    return df, problems
