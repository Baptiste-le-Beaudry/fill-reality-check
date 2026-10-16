"""Core analysis: locate every trade on 1m bars, run the checks, re-simulate the fills."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from . import __version__
from .fills import TradeRule, simulate
from .inputs import Rules
from .stats import (block_bootstrap, concentration, cscv_pbo, daily_series, deflated_sharpe,
                    summary)

MIN = 60_000_000_000  # ns per minute
STOP_REASONS = {"stop", "sl", "stop_loss", "stoploss", "trail", "trailing", "trailing_stop"}
# Public base-tier fees (percent per side). Hard-coded: verify against the current schedule.
BASE_FEES = {"spot": {"maker": 0.10, "taker": 0.10}, "um": {"maker": 0.02, "taker": 0.05}}
MODES = ("research", "close", "intrabar")
MODE_LABELS = {
    "declared": "Declared (prices in your CSV)",
    "research": "Re-simulated: stops checked on closes, filled at the stop level",
    "close": "Exit at the close that breached the level",
    "intrabar": "Resting stop intrabar (gap fills at the open)",
    "exec": "Executable: next-bar open entry + resting stops",
}


def _minutes(ts: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    v = pd.DatetimeIndex(ts).tz_convert("UTC").as_unit("ns").asi8
    return v // MIN


def _clusters(lo: np.ndarray, hi: np.ndarray, gap_min: int = 2 * 1440) -> list[tuple[int, int, np.ndarray]]:
    order = np.argsort(lo, kind="stable")
    out, cur, cs, ce = [], [], None, None
    for i in order:
        if cs is None or lo[i] > ce + gap_min:
            if cur:
                out.append((cs, ce, np.asarray(cur)))
            cur, cs, ce = [i], lo[i], hi[i]
        else:
            cur.append(i)
            ce = max(ce, hi[i])
    if cur:
        out.append((cs, ce, np.asarray(cur)))
    return out


def _rule_for(row, rules: Rules, max_bars: int) -> TradeRule:
    stop = row.stop_d if pd.notna(row.stop_d) else (rules.stop_pct / 100 if rules.stop_pct else None)
    target = row.target_d if pd.notna(row.target_d) else (rules.target_pct / 100 if rules.target_pct else None)
    ta = rules.trail_activation_pct / 100 if rules.trail_activation_pct is not None else None
    td = rules.trail_distance_pct / 100 if rules.trail_distance_pct is not None else None
    return TradeRule(max_bars=max_bars, stop=stop, target=target, trail_activation=ta, trail_distance=td)


def locate_and_simulate(trades: pd.DataFrame, rules: Rules, loader, verbose: bool = True) -> pd.DataFrame:
    """One output row per input trade, with flags and re-simulated gross returns."""
    lats = sorted(set(int(x) for x in rules.latency_bars) | {0})
    maxlat = max(lats)
    tol = rules.price_tolerance_pct / 100
    rows = {}
    t = trades
    sig = t.signal_time.where(t.signal_time.notna(), t.entry_time)
    lo = _minutes(sig.fillna(t.entry_time).fillna(pd.Timestamp(0, tz="UTC"))) - 5
    fixed = rules.max_bars if isinstance(rules.max_bars, int) else 0
    hi_entry = _minutes(t.entry_time.fillna(pd.Timestamp(0, tz="UTC")))
    hi_exit = _minutes(t.exit_time.fillna(pd.Timestamp(0, tz="UTC")))
    hi = np.maximum(hi_exit, hi_entry + fixed) + maxlat + 5
    for sym in sorted(t.symbol[t.valid].unique()):
        sel = np.flatnonzero((t.symbol == sym).to_numpy() & t.valid.to_numpy())
        for cs, ce, members in _clusters(lo[sel], hi[sel]):
            members = sel[members]
            start = pd.Timestamp(int(cs) * MIN, tz="UTC")
            end = pd.Timestamp(int(ce) * MIN, tz="UTC")
            if verbose:
                print(f"{sym}: {len(members)} trades, bars {start:%Y-%m-%d} -> {end:%Y-%m-%d}", flush=True)
            try:
                bars = loader.load(sym, start, end)
            except FileNotFoundError as e:
                for i in members:
                    rows[i] = {"status": "no_bars", "status_detail": str(e)}
                continue
            ts = _minutes(bars.index)
            o, h, l, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
            del bars
            for i in members:
                rows[i] = _one(t.iloc[i], ts, o, h, l, c, rules, lats, tol)
    res = pd.DataFrame.from_dict(rows, orient="index")
    out = trades.join(res, how="left") if len(res) else trades.assign(status=np.nan)
    out["status"] = out.status.fillna("invalid_input")
    for col in [*MODES, *(f"exec_L{k}" for k in lats), "exec_reason"]:
        if col not in out:
            out[col] = np.nan
    return out


def _one(r, ts, o, h, l, c, rules, lats, tol) -> dict:
    def at(minute):
        k = np.searchsorted(ts, minute)
        return int(k) if k < len(ts) and ts[k] == minute else -1

    e_min = int(_minutes(pd.DatetimeIndex([r.entry_time]))[0])
    x_min = int(_minutes(pd.DatetimeIndex([r.exit_time]))[0])
    be, bx = at(e_min), at(x_min)
    out: dict = {"status": "ok"}
    if be < 0 or bx < 0:
        return {"status": "bar_missing", "status_detail": "no bar at the entry or exit minute"}
    side = int(r.side_int)
    P, X = float(r.entry_price), float(r.exit_price)
    # K1/K2 impossible prices
    out["entry_outside_range"] = bool(P < l[be] * (1 - tol) or P > h[be] * (1 + tol))
    out["exit_outside_range"] = bool(X < l[bx] * (1 - tol) or X > h[bx] * (1 + tol))
    out["entry_bar_low"], out["entry_bar_high"] = float(l[be]), float(h[be])
    out["exit_bar_low"], out["exit_bar_high"] = float(l[bx]), float(h[bx])
    # signal bar
    if pd.notna(r.signal_time):
        s = at(int(_minutes(pd.DatetimeIndex([r.signal_time]))[0]))
        if s < 0:
            return {**out, "status": "bar_missing", "status_detail": "no bar at the signal minute"}
        out["entry_at_signal_close"] = bool(abs(P - c[s]) <= 1e-9 * P)
        # stamped before the signal bar closed AND not at its close price (that case is hint H2)
        out["entry_before_signal_close"] = bool(r.entry_time < r.signal_time + pd.Timedelta(minutes=1)
                                                and not out["entry_at_signal_close"])
        out["signal_source"] = "signal_time"
    else:
        at_close = abs(P - c[be]) <= abs(P - o[be])
        s = be if at_close else be - 1
        out["entry_before_signal_close"] = False
        out["entry_at_signal_close"] = bool(at_close and abs(P - c[be]) <= 1e-9 * P)
        out["signal_source"] = "entry_bar_close" if at_close else "previous_bar"
        if s < 0:
            return {**out, "status": "bar_missing", "status_detail": "no bar before the entry"}
    out["stamped_at_bar_open_filled_at_close"] = bool(abs(P - c[be]) <= 1e-9 * P and abs(P - o[be]) > 1e-9 * P)
    n_decl = max(1, bx - s)
    max_bars = rules.max_bars if isinstance(rules.max_bars, int) else n_decl
    rule = _rule_for(r, rules, max_bars)
    # K: stop touched intrabar before the declared exit (a resting stop would have fired)
    out["stop_touched_before_exit"] = False
    if rule.stop is not None:
        L = P * (1 - side * rule.stop)
        a = s + 1
        if a < bx:
            path = l[a:bx] if side == 1 else h[a:bx]
            out["stop_touched_before_exit"] = bool((path <= L).any() if side == 1 else (path >= L).any())
    # K: stop-type exit filled better than a gap open
    reason = str(r.exit_reason).strip().lower() if pd.notna(r.exit_reason) else ""
    out["stop_fill_better_than_gap_open"] = bool(reason in STOP_REASONS and side * (X - o[bx]) > tol * X)
    # simulations on a window
    w_end = min(len(c), s + max_bars + max(lats) + 2)
    wo, wh, wl, wc = o[s:w_end], h[s:w_end], l[s:w_end], c[s:w_end]
    for m in MODES:
        sim = simulate(wo, wh, wl, wc, 0, rule, side, "signal_close", m, 0)
        if sim is None:
            return {**out, "status": "no_entry_bar"}
        out[m] = sim["gross_ret"]
        out[f"{m}_reason"] = sim["exit_reason"]
    for lat in lats:
        sim = simulate(wo, wh, wl, wc, 0, rule, side, "next_open", "intrabar", lat)
        out[f"exec_L{lat}"] = np.nan if sim is None else sim["gross_ret"]
        if lat == 0 and sim is not None:
            out["exec_reason"] = sim["exit_reason"]
            out["exec_entry_price"] = sim["entry_price"]
    span = min(len(ts) - 1, s + max_bars + max(lats) + 1) - s
    out["gap_in_window"] = bool(ts[s + span] - ts[s] != span)
    out["end_of_data"] = any(out.get(f"{m}_reason") == "end_of_data" for m in MODES)
    out["max_bars_used"] = int(max_bars)
    return out


def overlaps(df: pd.DataFrame) -> pd.Series:
    """True for a trade that opens while an earlier trade on the same symbol is still open."""
    flag = pd.Series(False, index=df.index)
    for _, g in df.sort_values("entry_time").groupby("symbol"):
        open_until = None
        for i, r in g.iterrows():
            if open_until is not None and r.entry_time < open_until:
                flag[i] = True
                open_until = max(open_until, r.exit_time)
            else:
                open_until = r.exit_time if open_until is None else max(open_until, r.exit_time)
    return flag


def max_concurrent(df: pd.DataFrame) -> int:
    if not len(df):
        return 0
    times = np.concatenate([_minutes(df.entry_time), _minutes(df.exit_time)])
    delta = np.concatenate([np.ones(len(df), int), -np.ones(len(df), int)])
    order = np.lexsort((delta, times))  # at the same minute, exits (-1) come before entries
    return int(np.cumsum(delta[order]).max())


def kept_one_per_symbol(df: pd.DataFrame) -> pd.Series:
    """Greedy first-come filter: keep a trade only if no kept trade on the symbol is open."""
    keep = pd.Series(False, index=df.index)
    for _, g in df.sort_values("entry_time").groupby("symbol"):
        busy = None
        for i, r in g.iterrows():
            if busy is None or r.entry_time >= busy:
                keep[i] = True
                busy = r.exit_time
    return keep


def analyze(trades: pd.DataFrame, problems: list, rules: Rules, loader, trials_matrix: Optional[pd.DataFrame] = None,
            verbose: bool = True) -> dict:
    t = locate_and_simulate(trades, rules, loader, verbose)
    cost = t.fee_per_side_pct.where(t.fee_per_side_pct.notna(), rules.fee_per_side_pct)
    t["cost_rt"] = 2 * (cost + rules.slippage_per_side_pct) / 100
    ok = t.status == "ok"
    for col in ["exec_L%d" % k for k in sorted(set(int(x) for x in rules.latency_bars) | {0})]:
        ok &= t[col].notna()
    t["in_stats"] = ok
    p = t[ok].copy()
    lats = sorted(set(int(x) for x in rules.latency_bars) | {0})
    cols = {"declared": "declared_gross", **{m: m for m in MODES}, "exec": "exec_L0"}
    waterfall = []
    for key, col in cols.items():
        s = summary(p[col] - p.cost_rt, p[col])
        s["breakeven_rt_cost_pct"] = s["mean_gross_pct"]
        waterfall.append({"key": key, "label": MODE_LABELS[key], **s})
    for lat in lats[1:]:
        s = summary(p[f"exec_L{lat}"] - p.cost_rt, p[f"exec_L{lat}"])
        s["breakeven_rt_cost_pct"] = s["mean_gross_pct"]
        waterfall.append({"key": f"exec_L{lat}", "label": f"Executable + {lat} bar(s) latency", **s})
    # convention match: which simulation reproduces the declared returns?
    match = {}
    for key, col in cols.items():
        if key == "declared":
            continue
        match[key] = float((np.abs(p[col] - p.declared_gross) < 1e-6).mean()) if len(p) else float("nan")
    changed = int((np.abs(p.research - p.intrabar) > 1e-12).sum())
    # sensitivity grid (executable, extra cost per side x latency)
    grid = []
    for e in rules.extra_cost_per_side_pct:
        for lat in lats:
            net = p[f"exec_L{lat}"] - p.cost_rt - 2 * e / 100
            s = summary(net)
            grid.append({"extra_cost_per_side_pct": e, "latency_bars": lat, "pf": s["pf"],
                         "mean_net_pct": s["mean_net_pct"]})
        for key in ("declared", "research", "intrabar"):
            net = p[cols[key]] - p.cost_rt - 2 * e / 100
            s = summary(net)
            grid.append({"extra_cost_per_side_pct": e, "latency_bars": None, "mode": key, "pf": s["pf"],
                         "mean_net_pct": s["mean_net_pct"]})
    # overlap
    v = t[t.valid]
    ov = overlaps(v)
    t["overlap_same_symbol"] = False
    t.loc[ov.index, "overlap_same_symbol"] = ov
    keep = kept_one_per_symbol(p) if len(p) else pd.Series(dtype=bool)
    one_pos = {"n_overlapping": int(ov.sum()), "max_concurrent_all_symbols": max_concurrent(v) if len(v) else 0,
               "declared_one_position_per_symbol": rules.one_position_per_symbol}
    if len(p):
        q = p[keep]
        one_pos["kept_trades"] = int(len(q))
        one_pos["declared_kept"] = summary(q.declared_gross - q.cost_rt)
        one_pos["exec_kept"] = summary(q.exec_L0 - q.cost_rt)
    # statistics on the executable series
    exec_net = p.exec_L0 - p.cost_rt
    decl_net = p.declared_gross - p.cost_rt
    stats = {"dsr_exec": deflated_sharpe(exec_net, rules.n_trials, rules.trials_sr_variance),
             "dsr_declared": deflated_sharpe(decl_net, rules.n_trials, rules.trials_sr_variance)}
    stats["bootstrap_exec"] = block_bootstrap(daily_series(p.exit_time, exec_net), rules.bootstrap_block_days,
                                              rules.bootstrap_samples, rules.seed)
    stats["bootstrap_declared"] = block_bootstrap(daily_series(p.exit_time, decl_net), rules.bootstrap_block_days,
                                                  rules.bootstrap_samples, rules.seed)
    stats["concentration_declared"] = concentration(decl_net, p.exit_time)
    stats["concentration_exec"] = concentration(exec_net, p.exit_time)
    if trials_matrix is not None:
        stats["pbo"] = cscv_pbo(trials_matrix.to_numpy(float))
    yearly = []
    for y, g in p.groupby(p.entry_time.dt.year):
        yearly.append({"year": int(y), "n": int(len(g)),
                       **{f"{k}_mean_net_pct": 100 * float((g[c] - g.cost_rt).mean()) for k, c in cols.items()}})
    # fees vs public schedule
    base = BASE_FEES[rules.market]
    fee = {"declared_fee_per_side_pct": rules.fee_per_side_pct, "market": rules.market, **base,
           "below_base_maker": rules.fee_per_side_pct < base["maker"],
           "below_base_taker": rules.fee_per_side_pct < base["taker"]}
    statuses = t.status.value_counts().to_dict()
    return {"version": __version__, "rules": rules, "trades": t, "problems": problems, "waterfall": waterfall,
            "convention_match": match, "n_changed_research_vs_intrabar": changed, "grid": grid,
            "one_position": one_pos, "stats": stats, "yearly": yearly, "fees": fee, "statuses": statuses,
            "n_input": int(len(t)), "n_in_stats": int(ok.sum()),
            "period": (p.entry_time.min(), p.exit_time.max()) if len(p) else (None, None)}
