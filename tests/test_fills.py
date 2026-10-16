import numpy as np
import pytest

from fill_reality_check.fills import TradeRule, simulate


def arr(rows):
    a = np.asarray(rows, float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


# bar 0 = signal bar, close 100
PATH = [
    (100, 100, 100, 100),
    (100, 100.5, 98.8, 99.9),   # low touches -1.2 % intrabar, closes above the stop
    (99.9, 100.2, 98.4, 98.5),  # close breaches the -1 % stop
    (98.5, 99, 98, 98.2),
]


def test_research_close_intrabar_differ_on_same_path():
    o, h, l, c = arr(PATH)
    rule = TradeRule(max_bars=3, stop=0.01)
    r = simulate(o, h, l, c, 0, rule, 1, "signal_close", "research")
    k = simulate(o, h, l, c, 0, rule, 1, "signal_close", "close")
    i = simulate(o, h, l, c, 0, rule, 1, "signal_close", "intrabar")
    assert r["exit_idx"] == 2 and r["gross_ret"] == pytest.approx(-0.01)   # filled AT the level
    assert k["exit_idx"] == 2 and k["gross_ret"] == pytest.approx(-0.015)  # filled at the close
    assert i["exit_idx"] == 1 and i["gross_ret"] == pytest.approx(-0.01)   # the wick hits the resting stop first


def test_gap_through_stop_fills_at_open():
    o, h, l, c = arr([(100, 100, 100, 100), (97, 97.5, 96.5, 97.2)])
    i = simulate(o, h, l, c, 0, TradeRule(max_bars=1, stop=0.01), 1, "signal_close", "intrabar")
    assert i["gross_ret"] == pytest.approx(-0.03) and i["exit_reason"] == "stop"


def test_short_gap_symmetry():
    o, h, l, c = arr([(100, 100, 100, 100), (103, 103.5, 102.5, 103)])
    i = simulate(o, h, l, c, 0, TradeRule(max_bars=1, stop=0.01), -1, "signal_close", "intrabar")
    assert i["gross_ret"] == pytest.approx(-0.03)


def test_trailing_uses_peak_up_to_previous_bar():
    # bar 1 high +1 % arms the trail (activation 0.5 %, distance 0.4 %): level from bar 2 on = +0.6 %.
    # Bar 1 itself dips to +0.3 %, but the trail is not armed yet while bar 1 is tested.
    o, h, l, c = arr([(100, 100, 100, 100), (100.5, 101, 100.3, 100.9), (100.9, 100.9, 100.5, 100.6)])
    rule = TradeRule(max_bars=5, stop=0.02, trail_activation=0.005, trail_distance=0.004)
    i = simulate(o, h, l, c, 0, rule, 1, "signal_close", "intrabar")
    assert i["exit_idx"] == 2 and i["exit_reason"] == "trail"
    assert i["gross_ret"] == pytest.approx(0.006)


def test_target_needs_trade_through_and_stop_wins_same_bar():
    o, h, l, c = arr([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 101.2, 100.4, 101)])
    i = simulate(o, h, l, c, 0, TradeRule(max_bars=5, target=0.01), 1, "signal_close", "intrabar")
    assert i["exit_idx"] == 2 and i["gross_ret"] == pytest.approx(0.01)  # a touch at 101 is not a fill
    o, h, l, c = arr([(100, 100, 100, 100), (100, 102, 98, 100)])
    both = simulate(o, h, l, c, 0, TradeRule(max_bars=5, stop=0.01, target=0.01), 1, "signal_close", "intrabar")
    assert both["exit_reason"] == "stop"


def test_next_open_entry_latency_and_time_exit():
    o, h, l, c = arr([(100, 100, 100, 100), (101, 101, 101, 101), (102, 102, 102, 102), (103, 104, 103, 104)])
    rule = TradeRule(max_bars=2)
    a = simulate(o, h, l, c, 0, rule, 1, "next_open", "intrabar", 0)
    assert a["entry_price"] == 101 and a["exit_idx"] == 2 and a["exit_reason"] == "time"
    b = simulate(o, h, l, c, 0, rule, 1, "next_open", "intrabar", 1)
    assert b["entry_price"] == 102 and b["exit_idx"] == 3 and b["gross_ret"] == pytest.approx(104 / 102 - 1)
    assert simulate(o, h, l, c, 0, rule, 1, "next_open", "intrabar", 3) is None
    e = simulate(o, h, l, c, 0, TradeRule(max_bars=10), 1, "next_open", "intrabar", 0)
    assert e["exit_reason"] == "end_of_data"


def test_research_trailing_fills_at_level_close_mode_at_close():
    o, h, l, c = arr([(100, 100, 100, 100), (100, 101, 100, 101), (101, 101, 100, 100.2)])
    rule = TradeRule(max_bars=5, trail_activation=0.005, trail_distance=0.005)
    r = simulate(o, h, l, c, 0, rule, 1, "signal_close", "research")
    k = simulate(o, h, l, c, 0, rule, 1, "signal_close", "close")
    assert r["gross_ret"] == pytest.approx(0.005) and k["gross_ret"] == pytest.approx(0.002)


def test_invalid_arguments():
    o, h, l, c = arr(PATH)
    with pytest.raises(ValueError):
        simulate(o, h, l, c, 0, TradeRule(max_bars=2), 1, "signal_close", "intrabar", latency=1)
    with pytest.raises(ValueError):
        TradeRule(max_bars=0)
    with pytest.raises(ValueError):
        TradeRule(max_bars=2, trail_activation=0.01)
