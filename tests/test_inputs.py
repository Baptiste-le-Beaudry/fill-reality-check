import pandas as pd
import pytest

from fill_reality_check.inputs import Rules, read_trades


def base(**kw):
    d = {"symbol": ["btcusdt", "BTC/USDT"], "side": ["long", "sell"],
         "entry_time": ["2024-03-01T00:01:00Z", "2024-03-01T00:05:00Z"], "entry_price": [100.0, 100.0],
         "exit_time": ["2024-03-01T00:03:00Z", "2024-03-01T00:09:00Z"], "exit_price": [101.0, 99.0]}
    d.update(kw)
    return pd.DataFrame(d)


def test_parse_and_declared_returns():
    t, probs = read_trades(base(stop_price=[99.0, 101.0]))
    assert list(t.symbol) == ["BTCUSDT", "BTCUSDT"] and list(t.side_int) == [1, -1]
    assert t.declared_gross.tolist() == pytest.approx([0.01, 0.01])
    assert t.stop_d.tolist() == pytest.approx([0.01, 0.01])
    assert t.valid.all() and not probs


def test_missing_column():
    with pytest.raises(ValueError, match="missing required"):
        read_trades(base().drop(columns=["exit_price"]))


def test_problems_are_counted():
    df = base(exit_time=["2024-03-01T00:00:00Z", "2024-03-01T00:09:00Z"], side=["long", "flat"],
              stop_price=[101.0, None])
    t, probs = read_trades(df)
    text = " ".join(p["problem"] for p in probs)
    assert "exit not after entry" in text and "unknown side" in text and "wrong side" in text
    assert not t.valid.any()


def test_duplicates():
    df = pd.concat([base().iloc[:1]] * 2, ignore_index=True)
    t, probs = read_trades(df)
    assert t.valid.sum() == 1 and any("duplicate trade" in p["problem"] for p in probs)


def test_rules_toml(tmp_path):
    f = tmp_path / "r.toml"
    f.write_text("[costs]\nfee_per_side_pct = 0.05\n[exit]\nmax_bars = 30\nstop_pct = 1.0\n", encoding="utf-8")
    r = Rules.from_file(f, n_trials=12)
    assert r.cost_rt == pytest.approx(0.001) and r.max_bars == 30 and r.n_trials == 12
    f.write_text("typo_key = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown rules keys"):
        Rules.from_file(f)
    f.write_text("trail_activation_pct = 1.0\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Rules.from_file(f)
