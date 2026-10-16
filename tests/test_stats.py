import numpy as np
import pandas as pd
import pytest

from fill_reality_check import stats


def test_profit_factor_and_drawdown():
    assert stats.profit_factor([0.02, -0.01, 0.01, -0.01]) == pytest.approx(1.5)
    assert np.isinf(stats.profit_factor([0.01]))
    assert stats.max_drawdown(np.array([1, 3, 1, 2, -1.0])) == pytest.approx(4.0)


def test_psr_and_expected_max_sr():
    assert stats.psr(0.0, 0.0, 100, 0.0, 3.0) == pytest.approx(0.5)
    assert stats.psr(0.2, 0.0, 400, 0.0, 3.0) > 0.99
    e10, e100 = stats.expected_max_sr(1 / 99, 10), stats.expected_max_sr(1 / 99, 100)
    assert 0 < e10 < e100
    assert stats.expected_max_sr(0.01, 1) == 0.0


def test_deflated_sharpe_needs_trials():
    x = np.random.default_rng(0).normal(0.001, 0.01, 500)
    a = stats.deflated_sharpe(x, None)
    b = stats.deflated_sharpe(x, 200)
    assert "DSR" not in a and b["DSR"] < a["PSR0"] and b["variance_assumed"]


def test_pbo_noise_vs_real_edge():
    rng = np.random.default_rng(3)
    noise = rng.normal(0, 0.01, (320, 12))
    assert 0.2 < stats.cscv_pbo(noise, S=8)["PBO"] < 0.8
    edge = noise.copy()
    edge[:, 0] += 0.006
    assert stats.cscv_pbo(edge, S=8)["PBO"] < 0.05


def test_block_bootstrap_is_seeded():
    d = pd.Series(np.random.default_rng(1).normal(0, 0.01, 200))
    a, b = stats.block_bootstrap(d, 5, 300, 9), stats.block_bootstrap(d, 5, 300, 9)
    assert a == b and a["total_p05_pct"] < a["total_p50_pct"] < a["total_p95_pct"]
    assert "note" in stats.block_bootstrap(d.iloc[:5], 5, 10)


def test_daily_series_zero_fills():
    t = pd.to_datetime(["2024-01-01 10:00", "2024-01-03 11:00"], utc=True)
    d = stats.daily_series(pd.Series(t), pd.Series([0.01, -0.02]))
    assert len(d) == 3 and d.iloc[1] == 0.0
