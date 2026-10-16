"""Security regressions: symbol validation, cache paths, URL encoding, CSV formula injection, HTML escaping."""
import csv
import urllib.parse

import pandas as pd
import pytest

from fill_reality_check import bars as B
from fill_reality_check.cli import main
from fill_reality_check.report import csv_safe, csv_safe_cell, write_csv
from fill_reality_check.synthetic import random_walk_bars, trapped_trades

BAD_SYMBOLS = ["..", "../ETCUSDT", "..\\..\\X", "BTC\\USDT", "BTC/USDT", "BTCUSDT?X=1", "BTC&limit=5",
               "BTC%2FUSDT", "C:X", "A", "", "btcusdt", "BTC USDT", "BTC\x00USDT", "X" * 21, "CON", "NUL",
               "COM1", "LPT9", None, 123]


@pytest.mark.parametrize("sym", BAD_SYMBOLS)
def test_validate_symbol_refuses(sym):
    with pytest.raises(B.InvalidSymbolError):
        B.validate_symbol(sym)


def test_validate_symbol_accepts_and_allow_list():
    assert B.validate_symbol("BTCUSDT") == "BTCUSDT"
    assert B.validate_symbol("1000PEPEUSDT") == "1000PEPEUSDT"
    assert B.validate_symbol("ETHUSDT", allowed={"BTCUSDT", "ETHUSDT"}) == "ETHUSDT"
    with pytest.raises(B.InvalidSymbolError, match="supported"):
        B.validate_symbol("SOLUSDT", allowed={"BTCUSDT"})


def test_safe_join_stays_inside(tmp_path):
    p = B.safe_join(tmp_path, "BTCUSDT", "BTCUSDT-2024-01.npz")
    assert tmp_path.resolve() in p.parents
    for parts in (("..", "x.npz"), ("a\\..\\..", "x"), ("a/b",), ("C:x",), ("CON.npz",), ("NUL",), ("x..y",)):
        with pytest.raises(B.InvalidSymbolError):
            B.safe_join(tmp_path, *parts)


def test_archive_refuses_before_any_network_or_file(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(B.BinanceArchive, "_get", lambda self, url, retries=3: calls.append(url))
    a = B.BinanceArchive("spot", tmp_path / "cache", verbose=False)
    s, e = pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC")
    for sym in ("..\\..\\EVIL", "../EVIL", "BTCUSDT&limit=1", "CON"):
        with pytest.raises(B.InvalidSymbolError):
            a.load(sym, s, e)
    assert calls == [] and not (tmp_path / "cache").exists()
    allow = B.BinanceArchive("spot", tmp_path / "cache", verbose=False, allowed_symbols={"BTCUSDT"})
    with pytest.raises(B.InvalidSymbolError, match="supported"):
        allow.load("ETHUSDT", s, e)


def test_rest_url_is_encoded(monkeypatch):
    urls = []
    monkeypatch.setattr(B.BinanceArchive, "_get", lambda self, url, retries=3: urls.append(url) or b"[]")
    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    a = B.BinanceArchive("um", verbose=False)
    a._rest("BTCUSDT", 1704067200000, 1704067260000)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query)
    assert q == {"symbol": ["BTCUSDT"], "interval": ["1m"], "startTime": ["1704067200000"],
                 "endTime": ["1704067259999"], "limit": ["1000"]}
    with pytest.raises(B.InvalidSymbolError):
        a._rest("BTCUSDT&limit=5", 0, 1)
    with pytest.raises(B.InvalidSymbolError):
        a._archive("../X", "monthly", "2024-01")
    with pytest.raises(ValueError):
        a._archive("BTCUSDT", "monthly", "2024-01/../../x")


def test_local_bars_refuse_bad_symbol(tmp_path):
    (tmp_path / "inner").mkdir()
    random_walk_bars(10).reset_index().to_csv(tmp_path / "EVILUSDT.csv", index=False)
    lb = B.LocalBars(tmp_path / "inner")
    for sym in ("..\\EVILUSDT", "../EVILUSDT"):
        with pytest.raises(B.InvalidSymbolError):
            lb.load(sym, pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"))
    with pytest.raises(B.InvalidSymbolError, match="outside"):  # the symbol is fine, the result is not
        B.LocalBars(tmp_path / "inner", "../{symbol}.csv").load("EVILUSDT", pd.Timestamp("2024-01-01", tz="UTC"),
                                                               pd.Timestamp("2024-01-02", tz="UTC"))


def test_csv_safe_cell():
    for v in ("=1+1", "+cmd", "-2+3", "@SUM(A1)", "\t=x", "\rx", "=HYPERLINK(\"http://x\")"):
        assert csv_safe_cell(v) == "'" + v
    for v in ("-1", "-1.5", "+2", "-1e-5", "BTCUSDT", "stop", "", -1, -1.5, None):
        assert csv_safe_cell(v) == v


def test_csv_safe_keeps_numeric_columns(tmp_path):
    df = pd.DataFrame({"trade_id": ["=cmd|' /C calc'!A0", "7"], "x": [-1.5, 2.0], "n": [-3, 4],
                       "exit_reason": ["@evil", "-1"]})
    out = csv_safe(df)
    assert out.trade_id.tolist() == ["'=cmd|' /C calc'!A0", "7"]
    assert out.exit_reason.tolist() == ["'@evil", "-1"]
    assert out.x.tolist() == [-1.5, 2.0] and out.n.tolist() == [-3, 4]
    path = tmp_path / "o.csv"
    write_csv(df, path, index=False)
    rows = list(csv.reader(path.open(encoding="utf-8")))
    assert rows[1][1] == "-1.5" and rows[1][2] == "-3" and rows[1][0].startswith("'=")


def _hostile_run(tmp_path):
    bars = {"SYNAUSDT": random_walk_bars(8000, seed=3)}
    bdir = tmp_path / "bars"
    bdir.mkdir()
    bars["SYNAUSDT"].reset_index().to_csv(bdir / "SYNAUSDT.csv", index=False)
    df = trapped_trades(bars, n_per_symbol=20, seed=4)
    df["trade_id"] = df["trade_id"].astype(str)
    df.loc[0, "trade_id"] = "=HYPERLINK(\"http://x\",\"<script>alert(1)</script>\")"
    df.loc[1, "symbol"] = "..\\..\\EVIL"
    df.loc[2, "symbol"] = "<img src=x onerror=alert(1)>"
    df.loc[3, "trade_id"] = "@SUM(1)"
    trades = tmp_path / "trades.csv"
    df.to_csv(trades, index=False)
    out = tmp_path / "rep"
    assert main([str(trades), "--bars-dir", str(bdir), "--out", str(out), "-q",
                 "--title", "<script>alert('t')</script>"]) == 0
    return out


def test_hostile_csv_end_to_end(tmp_path):
    out = _hostile_run(tmp_path)
    flagged = pd.read_csv(out / "flagged_trades.csv", dtype=str, keep_default_na=False)
    texts = [str(v) for v in flagged.to_numpy().ravel()]
    assert not [v for v in texts if v[:1] in ("=", "+", "@", "\t", "\r")]
    assert "invalid_symbol" in flagged.status.tolist()
    assert not list(tmp_path.parent.glob("EVIL*"))  # nothing written outside
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "<script" not in html.lower() and "<img" not in html.lower()
    assert "&lt;script&gt;" in html
