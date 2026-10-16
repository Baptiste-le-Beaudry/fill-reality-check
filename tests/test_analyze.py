import pandas as pd
import pytest

from conftest import DictBars, make_bars
from fill_reality_check.analyze import analyze, max_concurrent, overlaps
from fill_reality_check.inputs import Rules, read_trades
from fill_reality_check.report import findings
from fill_reality_check.synthetic import random_walk_bars, trapped_trades

T0 = pd.Timestamp("2024-03-01 00:00", tz="UTC")


def m(k):
    return (T0 + pd.Timedelta(minutes=k)).isoformat()


def flat_bars(n=40, px=100.0):
    return make_bars([(px, px * 1.001, px * 0.999, px)] * n)


def run(rows, bars, **rules):
    t, probs = read_trades(pd.DataFrame(rows))
    return analyze(t, probs, Rules(**rules), DictBars(bars), verbose=False)


def test_price_outside_range_and_entry_before_signal_close():
    rows = [{"symbol": "AAAUSDT", "side": "long", "signal_time": m(5), "entry_time": m(5), "entry_price": 100.5,
             "exit_time": m(9), "exit_price": 100.0}]
    res = run(rows, {"AAAUSDT": flat_bars()})
    t = res["trades"].iloc[0]
    assert t.entry_outside_range and not t.exit_outside_range
    assert t.entry_before_signal_close
    codes = {f["code"] for f in findings(res)}
    assert {"C1", "C3"} <= codes


def test_research_convention_is_detected_and_breakdown():
    rows_bars = [(99.9, 100, 99.9, 100)] + [(100, 100.2, 98.7, 99.8)] * 3 + [(99.8, 99.9, 98.5, 98.6)] + [(98.6, 99, 98.4, 98.8)] * 10
    bars = {"AAAUSDT": make_bars(rows_bars)}
    # declared: entry at close of bar 0, stop 1 % checked on closes, filled at 99.0 on bar 4
    rows = [{"symbol": "AAAUSDT", "side": "long", "entry_time": m(0), "entry_price": 100.0,
             "exit_time": m(4), "exit_price": 99.0, "stop_price": 99.0, "exit_reason": "stop"}]
    res = run(rows, bars, fee_per_side_pct=0.0, max_bars=10)
    t = res["trades"].iloc[0]
    assert t.research == pytest.approx(-0.01) and t.close == pytest.approx(-0.014)
    assert t.intrabar == pytest.approx(-0.01) and t.intrabar_reason == "stop"
    assert t.stop_touched_before_exit  # lows at 98.7 on bars 1-3
    assert res["convention_match"]["research"] == 1.0
    assert t.stamped_at_bar_open_filled_at_close


def test_stop_fill_better_than_gap_open():
    bars = {"AAAUSDT": make_bars([(100, 100, 100, 100), (100, 100.1, 99.9, 100), (97, 97.2, 96.8, 97)] + [(97, 97, 97, 97)] * 5)}
    rows = [{"symbol": "AAAUSDT", "side": "long", "entry_time": m(0), "entry_price": 100.0, "exit_time": m(2),
             "exit_price": 99.0, "stop_price": 99.0, "exit_reason": "stop"}]
    res = run(rows, bars, fee_per_side_pct=0.0)
    t = res["trades"].iloc[0]
    assert t.stop_fill_better_than_gap_open
    assert t.intrabar == pytest.approx(-0.03)


def test_overlaps_and_concurrency():
    t, _ = read_trades(pd.DataFrame([
        {"symbol": "A", "side": "long", "entry_time": m(0), "entry_price": 1, "exit_time": m(10), "exit_price": 1},
        {"symbol": "A", "side": "long", "entry_time": m(5), "entry_price": 1, "exit_time": m(8), "exit_price": 1},
        {"symbol": "A", "side": "long", "entry_time": m(10), "entry_price": 1, "exit_time": m(12), "exit_price": 1},
        {"symbol": "B", "side": "long", "entry_time": m(6), "entry_price": 1, "exit_time": m(7), "exit_price": 1}]))
    assert overlaps(t).tolist() == [False, True, False, False]
    assert max_concurrent(t) == 3


def test_missing_bars_are_counted_not_dropped():
    rows = [{"symbol": "ZZZUSDT", "side": "long", "entry_time": m(0), "entry_price": 1, "exit_time": m(3), "exit_price": 1}]
    res = run(rows, {"AAAUSDT": flat_bars()})
    assert res["n_input"] == 1 and res["n_in_stats"] == 0 and res["statuses"] == {"no_bars": 1}


def test_trapped_backtest_end_to_end():
    bars = {"SYNAUSDT": random_walk_bars(15000, seed=11), "SYNBUSDT": random_walk_bars(15000, seed=12, price=20)}
    df = trapped_trades(bars, n_per_symbol=80, seed=5)
    t, probs = read_trades(df)
    res = analyze(t, probs, Rules(fee_per_side_pct=0.05, trail_activation_pct=0.4, trail_distance_pct=0.5,
                                  max_bars=45, n_trials=50), DictBars(bars), verbose=False)
    codes = {f["code"] for f in findings(res)}
    assert {"C1", "C3", "C5", "C7"} <= codes
    wf = {w["key"]: w for w in res["waterfall"]}
    assert res["convention_match"]["research"] > 0.9
    assert wf["intrabar"]["mean_net_pct"] < wf["research"]["mean_net_pct"]
    assert "DSR" in res["stats"]["dsr_exec"]
