"""Bar-level fill simulation for one trade.

Provenance: adapted from an internal execution harness (``execution_harness/fills.py``,
v1.4.0) and its exit-realism diagnostic. The resting-stop rules, the gap-aware fill
``min(level, open)`` and the "trailing level from the peak up to bar t-1" convention
are unchanged; this version adds the two close-checked conventions used to audit
research backtests, optional take-profit targets, and works on short windows.

Conventions
-----------
* Bars are 1-minute OHLC, labelled by their OPEN time, positional (row i+1 is the
  next bar). A signal on bar ``s`` is known at the CLOSE of ``s``.
* Entry modes
    - ``signal_close``: fill at ``close[s]``. Only reachable if the order is sent
      before the bar closes, i.e. NOT executable when the signal uses that close.
      Used to reproduce research ledgers.
    - ``next_open``: fill at ``open[s + 1 + latency]`` (the executable default).
* Evaluated bars: ``j .. j + max_bars - 1`` with ``j = s + 1 + latency``. The time
  exit fills at the CLOSE of the last evaluated bar.
* Exit modes
    - ``research``: stop / trailing / target checked on bar CLOSES, filled AT the
      level. This is the common research shortcut and cannot be executed: a rule
      that only looks at closes can only exit at a close.
    - ``close``: same checks on closes, filled at that CLOSE (what a bot that polls
      closes can actually get).
    - ``intrabar``: resting orders. Stop / trailing tested on the bar's low (long)
      or high (short); a gap through the level fills at the OPEN
      (``min(level, open)`` for a long). The trailing level on bar t uses the peak
      observed up to bar t-1 only. A take-profit is a resting limit: it fills at the
      target only if the bar trades THROUGH it (``high > target`` for a long), never
      better than the target (conservative). If stop and target are both reachable
      in the same bar, the stop wins (conservative).
* All levels are in return space relative to the entry price, so a re-simulation
  with a different entry price keeps the declared stop / target DISTANCES.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

ENTRY_MODES = ("signal_close", "next_open")
EXIT_MODES = ("research", "close", "intrabar")


@dataclass(frozen=True)
class TradeRule:
    """Exit rule for one trade, distances as fractions (0.01 = 1 %).

    stop : protective stop distance (None = no stop).
    target : take-profit distance (None = no target).
    trail_activation / trail_distance : trailing stop, armed once the peak
        favourable return reaches ``trail_activation``; level = peak - distance.
    max_bars : time exit at the close of the ``max_bars``-th evaluated bar.
    """
    max_bars: int
    stop: Optional[float] = None
    target: Optional[float] = None
    trail_activation: Optional[float] = None
    trail_distance: Optional[float] = None

    def __post_init__(self):
        if self.max_bars < 1:
            raise ValueError("max_bars must be >= 1")
        if (self.trail_activation is None) != (self.trail_distance is None):
            raise ValueError("trail_activation and trail_distance go together")
        for name in ("stop", "target", "trail_distance"):
            v = getattr(self, name)
            if v is not None and v <= 0:
                raise ValueError(f"{name} must be > 0")


def simulate(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, s: int,
             rule: TradeRule, side: int = 1, entry_mode: str = "next_open",
             exit_mode: str = "intrabar", latency: int = 0) -> Optional[dict]:
    """Simulate one trade on positional OHLC arrays; ``s`` is the signal bar index.

    Returns None if the entry bar does not exist. Otherwise a dict with
    ``entry_idx, entry_price, exit_idx, exit_price, exit_reason`` ('stop', 'trail',
    'target', 'time', 'end_of_data') and ``gross_ret`` (side-signed fraction).
    """
    if entry_mode not in ENTRY_MODES:
        raise ValueError(f"entry_mode must be one of {ENTRY_MODES}")
    if exit_mode not in EXIT_MODES:
        raise ValueError(f"exit_mode must be one of {EXIT_MODES}")
    if side not in (1, -1):
        raise ValueError("side must be +1 (long) or -1 (short)")
    if latency < 0:
        raise ValueError("latency must be >= 0")
    if entry_mode == "signal_close" and latency:
        raise ValueError("signal_close is a reconciliation mode; latency is not allowed")
    n = len(c)
    first = s + 1 + latency
    if s < 0 or first >= n:
        return None
    entry = float(c[s]) if entry_mode == "signal_close" else float(o[first])
    stop = -rule.stop if rule.stop is not None else -np.inf
    target = rule.target if rule.target is not None else np.inf
    trailing = rule.trail_activation is not None
    tdist = rule.trail_distance if trailing else 0.0
    peak, active = -np.inf, False
    last = min(first + rule.max_bars - 1, n - 1)

    def ret(px):
        return side * (px / entry - 1.0)

    exit_idx = exit_ret = reason = None
    for t in range(first, last + 1):
        if exit_mode == "intrabar":
            if side == 1:
                adverse, favourable = ret(l[t]), ret(h[t])
            else:
                adverse, favourable = ret(h[t]), ret(l[t])
            op = ret(o[t])
            level = max(stop, peak - tdist) if (trailing and active) else stop
            if adverse <= level:
                exit_idx, exit_ret = t, min(level, op)
                reason = "trail" if (trailing and active and level > stop) else "stop"
                break
            if favourable > target + 1e-12:  # trade-through, not a touch
                exit_idx, exit_ret, reason = t, target, "target"
                break
            peak = max(peak, favourable)
            if trailing:
                active = active or peak >= rule.trail_activation
        else:
            x = ret(c[t])
            if x <= stop:
                exit_idx, exit_ret, reason = t, (stop if exit_mode == "research" else x), "stop"
                break
            if x >= target:
                exit_idx, exit_ret, reason = t, (target if exit_mode == "research" else x), "target"
                break
            peak = max(peak, x)
            if trailing:
                active = active or peak >= rule.trail_activation
                if active and x <= peak - tdist:
                    exit_idx, reason = t, "trail"
                    exit_ret = (peak - tdist) if exit_mode == "research" else x
                    break
    if exit_idx is None:
        exit_idx = last
        exit_ret = ret(c[last])
        reason = "time" if last == first + rule.max_bars - 1 else "end_of_data"
    return {"entry_idx": s if entry_mode == "signal_close" else first, "entry_price": entry,
            "exit_idx": int(exit_idx), "exit_price": float(entry * (1.0 + side * exit_ret)),
            "exit_reason": reason, "gross_ret": float(exit_ret)}
