import hashlib
import io
import re
import zipfile

import numpy as np
import pandas as pd
import pytest

from fill_reality_check import bars as B
from fill_reality_check.cli import main
from fill_reality_check.synthetic import random_walk_bars, trapped_trades


def test_parse_kline_csv_header_and_microseconds():
    spot = b"1704067200000000,1,2,0.5,1.5,10,0,0,0,0,0,0\n1704067260000000,1.5,2,1,1.8,10,0,0,0,0,0,0\n"
    t, x = B._parse_kline_csv(spot)
    assert t.tolist() == [1704067200000, 1704067260000] and x[1, 3] == 1.8
    fut = b"open_time,open,high,low,close,volume\n1704067200000,1,2,0.5,1.5,10\n"
    t, x = B._parse_kline_csv(fut)
    assert t.tolist() == [1704067200000] and x.shape == (1, 4)


def _zip(raw: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("X-1m-2024-01.csv", raw)
    return buf.getvalue()


def test_archive_checksum_and_cache(tmp_path, monkeypatch):
    raw = b"1704067200000,1,2,0.5,1.5,10\n1704067260000,1.5,2,1,1.8,10\n"
    blob = _zip(raw)
    calls = []

    def fake_get(self, url, retries=3):
        calls.append(url)
        if url.endswith(".CHECKSUM"):
            return f"{hashlib.sha256(blob).hexdigest()}  x.zip".encode()
        return blob if "monthly" in url else None

    monkeypatch.setattr(B.BinanceArchive, "_get", fake_get)
    a = B.BinanceArchive("spot", tmp_path, verbose=False)
    s, e = pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-01 00:05", tz="UTC")
    df = a.load("XUSDT", s, e)
    assert len(df) == 2 and "data/spot/monthly/klines/XUSDT/1m/XUSDT-1m-2024-01.zip" in calls[0]
    n = len(calls)
    a.load("XUSDT", s, e)  # complete past month: served from the cache
    assert len(calls) == n
    off = B.BinanceArchive("spot", tmp_path / "empty", offline=True, verbose=False)
    with pytest.raises(RuntimeError):
        off.load("XUSDT", s, e)


def test_archive_checksum_mismatch(tmp_path, monkeypatch):
    blob = _zip(b"1704067200000,1,2,0.5,1.5,10\n")
    monkeypatch.setattr(B.BinanceArchive, "_get",
                        lambda self, url, retries=3: b"00  x" if url.endswith(".CHECKSUM") else blob)
    with pytest.raises(ValueError, match="checksum"):
        B.BinanceArchive("spot", tmp_path, verbose=False).load(
            "XUSDT", pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-02", tz="UTC"))


def test_local_bars_csv_and_pickle_refusal(tmp_path):
    b = random_walk_bars(100)
    b.reset_index().to_csv(tmp_path / "AUSDT.csv", index=False)
    b.to_pickle(tmp_path / "AUSDT.pkl")
    df = B.LocalBars(tmp_path).load("AUSDT", b.index[0], b.index[-1])
    assert len(df) == 100 and df.index.tz is not None
    with pytest.raises(ValueError, match="unpickle"):
        B.LocalBars(tmp_path, "{symbol}.pkl").load("AUSDT", b.index[0], b.index[-1])
    assert len(B.LocalBars(tmp_path, "{symbol}.pkl", allow_pickle=True).load("AUSDT", b.index[0], b.index[-1])) == 100


def test_cli_end_to_end_offline(tmp_path):
    bars = {"SYNAUSDT": random_walk_bars(8000, seed=3)}
    bdir = tmp_path / "bars"
    bdir.mkdir()
    bars["SYNAUSDT"].reset_index().to_csv(bdir / "SYNAUSDT.csv", index=False)
    trades = tmp_path / "trades.csv"
    trapped_trades(bars, n_per_symbol=40, seed=4).to_csv(trades, index=False)
    rules = tmp_path / "rules.toml"
    rules.write_text("fee_per_side_pct = 0.05\ntrail_activation_pct = 0.4\ntrail_distance_pct = 0.5\nmax_bars = 45\n",
                     encoding="utf-8")
    out = tmp_path / "out"
    assert main([str(trades), "--rules", str(rules), "--bars-dir", str(bdir), "--out", str(out), "-q",
                 "--n-trials", "20"]) == 0
    md = (out / "report.md").read_text(encoding="utf-8")
    h = (out / "report.html").read_text(encoding="utf-8")
    assert "## Certain errors" in md and "Deflated Sharpe Ratio" in md
    assert "<svg" in h and not re.search(r"(src|href)=['\"]https?://", h)  # self-contained
    assert (out / "flagged_trades.csv").exists() and (out / "summary.json").exists()
