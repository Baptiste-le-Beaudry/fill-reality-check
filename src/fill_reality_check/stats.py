"""Statistics: profit factor, PSR / Deflated Sharpe Ratio, PBO (CSCV), block bootstrap.

Provenance: formulas adapted from an internal validation script (DSR / CSCV,
Bailey & Lopez de Prado 2014; Bailey, Borwein, Lopez de Prado & Zhu 2017),
rewritten without SciPy.
"""
from __future__ import annotations

import itertools
import math
from statistics import NormalDist
from typing import Optional

import numpy as np
import pandas as pd

EULER = 0.5772156649015329
_N = NormalDist()


def profit_factor(x) -> float:
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    gains, losses = x[x > 0].sum(), -x[x < 0].sum()
    if losses == 0:
        return float("inf") if gains > 0 else float("nan")
    return float(gains / losses)


def summary(net, gross=None) -> dict:
    net = np.asarray(net, float)
    net = net[~np.isnan(net)]
    out = {"n": int(len(net)), "pf": profit_factor(net),
           "mean_net_pct": 100 * float(net.mean()) if len(net) else float("nan"),
           "sum_net_pct": 100 * float(net.sum()),
           "win_rate_pct": 100 * float((net > 0).mean()) if len(net) else float("nan")}
    if gross is not None:
        g = np.asarray(gross, float)
        g = g[~np.isnan(g)]
        out["mean_gross_pct"] = 100 * float(g.mean()) if len(g) else float("nan")
    return out


def moments(x) -> dict:
    s = pd.Series(np.asarray(x, float)).dropna()
    sd = s.std(ddof=1)
    return {"T": int(len(s)), "SR": float(s.mean() / sd) if sd > 0 else float("nan"),
            "skew": float(s.skew()), "kurt": float(s.kurt() + 3.0)}  # Pearson kurtosis (normal = 3)


def _sr_se(sr: float, T: int, skew: float, kurt: float) -> float:
    """Std error of the Sharpe ratio estimator, non-normal iid returns (Mertens 2002)."""
    return math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr ** 2, 1e-12) / (T - 1))


def psr(sr: float, sr0: float, T: int, skew: float, kurt: float) -> float:
    """Probabilistic Sharpe Ratio: P(true SR > sr0)."""
    return float(_N.cdf((sr - sr0) / _sr_se(sr, T, skew, kurt)))


def expected_max_sr(variance: float, n_trials: float) -> float:
    """Expected maximum of ``n_trials`` independent SR estimates with true SR 0 and the
    given variance (False Strategy Theorem)."""
    if n_trials <= 1:
        return 0.0
    return float(math.sqrt(variance) * ((1 - EULER) * _N.inv_cdf(1 - 1 / n_trials)
                                        + EULER * _N.inv_cdf(1 - 1 / (n_trials * math.e))))


def min_trl(sr: float, sr0: float, skew: float, kurt: float, alpha: float = 0.05) -> float:
    """Minimum track record length (in observations) for PSR(sr0) >= 1 - alpha."""
    if not sr > sr0:
        return float("inf")
    z = _N.inv_cdf(1 - alpha)
    return float(1 + (1 - skew * sr + (kurt - 1) / 4 * sr ** 2) * (z / (sr - sr0)) ** 2)


def deflated_sharpe(x, n_trials: Optional[int], trials_sr_variance: Optional[float] = None) -> dict:
    """PSR(0) always; DSR when ``n_trials`` is given. Without a measured variance of the
    trials' Sharpe ratios, the variance of the SR estimator under the null, 1/(T-1), is
    used: it assumes all trials were pure noise of the same length (stated in the report)."""
    m = moments(x)
    T, sr = m["T"], m["SR"]
    if T < 10 or not np.isfinite(sr):
        return {**m, "PSR0": float("nan")}
    out = {**m, "PSR0": psr(sr, 0.0, T, m["skew"], m["kurt"]),
           "MinTRL_vs_0": min_trl(sr, 0.0, m["skew"], m["kurt"])}
    if n_trials:
        var = trials_sr_variance if trials_sr_variance else 1.0 / (T - 1)
        sr0 = expected_max_sr(var, n_trials)
        out.update({"n_trials": int(n_trials), "trials_sr_variance": var,
                    "variance_assumed": trials_sr_variance is None, "SR0": sr0,
                    "DSR": psr(sr, sr0, T, m["skew"], m["kurt"]),
                    "MinTRL_vs_SR0": min_trl(sr, sr0, m["skew"], m["kurt"])})
    return out


def _rank(a: np.ndarray) -> np.ndarray:
    return pd.Series(a).rank(method="average").to_numpy()


def cscv_pbo(M: np.ndarray, S: int = 16) -> dict:
    """Probability of Backtest Overfitting by combinatorially symmetric cross-validation.
    ``M``: T x N matrix of per-period returns, one column per variant tried."""
    M = np.asarray(M, float)
    T, N = M.shape
    if N < 2 or T < S * 2:
        raise ValueError("PBO needs >= 2 variants and >= 2*S periods")
    blocks = np.array_split(np.arange(T), S)
    logits, oos_best = [], []
    for comb in itertools.combinations(range(S), S // 2):
        ins = np.concatenate([blocks[i] for i in comb])
        oos = np.concatenate([blocks[i] for i in range(S) if i not in comb])
        a, b = M[ins], M[oos]
        sa, sb = a.std(0), b.std(0)
        sr_is = a.mean(0) / np.where(sa > 0, sa, np.inf)
        sr_oos = b.mean(0) / np.where(sb > 0, sb, np.inf)
        k = int(np.argmax(sr_is))
        w = _rank(sr_oos)[k] / (N + 1)
        logits.append(math.log(w / (1 - w)))
        oos_best.append(sr_oos[k])
    logits = np.asarray(logits)
    return {"S": S, "n_combinations": len(logits), "n_variants": N, "PBO": float((logits <= 0).mean()),
            "median_logit": float(np.median(logits)),
            "P_oos_sr_of_is_best_below_0": float((np.asarray(oos_best) < 0).mean())}


def daily_series(exit_times: pd.Series, net: pd.Series) -> pd.Series:
    """Sum of per-trade net returns by UTC exit day, zero-filled over the calendar range."""
    d = pd.Series(np.asarray(net, float), index=pd.DatetimeIndex(exit_times).tz_convert("UTC").floor("D"))
    d = d.groupby(level=0).sum()
    if not len(d):
        return d
    full = pd.date_range(d.index.min(), d.index.max(), freq="D")
    return d.reindex(full, fill_value=0.0)


def max_drawdown(cum: np.ndarray) -> float:
    """Largest peak-to-trough fall of an additive cumulative series (positive number)."""
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))
    return float((peak[1:] - cum).max()) if len(cum) else 0.0


def block_bootstrap(daily: pd.Series, block_days: int = 5, samples: int = 2000, seed: int = 7) -> dict:
    """Circular block bootstrap of the daily net series (blocks of days, not of trades,
    so that same-day clustering is preserved). Additive returns at constant notional."""
    x = np.asarray(daily, float)
    n = len(x)
    if n < 2 * block_days:
        return {"n_days": n, "note": "too few days for a block bootstrap"}
    rng = np.random.default_rng(seed)
    nb = int(math.ceil(n / block_days))
    totals, dds = np.empty(samples), np.empty(samples)
    offs = np.arange(block_days)
    for k in range(samples):
        starts = rng.integers(0, n, nb)
        idx = ((starts[:, None] + offs[None, :]) % n).ravel()[:n]
        path = np.cumsum(x[idx])
        totals[k], dds[k] = path[-1], max_drawdown(path)
    q = lambda a, p: 100 * float(np.quantile(a, p))  # noqa: E731
    return {"n_days": n, "block_days": block_days, "samples": samples, "seed": seed,
            "total_p05_pct": q(totals, .05), "total_p50_pct": q(totals, .5), "total_p95_pct": q(totals, .95),
            "p_total_le_0": float((totals <= 0).mean()),
            "maxdd_p50_pct": q(dds, .5), "maxdd_p95_pct": q(dds, .95),
            "observed_total_pct": 100 * float(x.sum()), "observed_maxdd_pct": 100 * max_drawdown(np.cumsum(x))}


def concentration(net: pd.Series, exit_times: pd.Series) -> dict:
    x = pd.Series(np.asarray(net, float)).dropna().sort_values(ascending=False)
    n = len(x)
    if not n:
        return {}
    out = {"sum_pct": 100 * float(x.sum())}
    for frac, name in ((0.01, "top1pct"), (0.05, "top5pct")):
        k = max(1, int(round(n * frac)))
        out[f"sum_without_{name}_trades_pct"] = 100 * float(x.iloc[k:].sum())
        out[f"n_{name}"] = k
    d = daily_series(exit_times, net).sort_values(ascending=False)
    out["sum_without_top5_days_pct"] = 100 * float(d.iloc[5:].sum())
    return out
