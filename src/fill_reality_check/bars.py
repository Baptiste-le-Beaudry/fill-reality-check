"""1-minute bars: public Binance archives with a local cache, or a local directory.

Sources
-------
* ``BinanceArchive``: monthly (then daily) kline zips from https://data.binance.vision,
  verified against the published SHA-256 ``.CHECKSUM``; falls back to the public REST
  endpoint for the most recent days that are not archived yet. Markets: ``spot`` and
  ``um`` (USD-M perpetual futures). No API key is used or needed.
  Each symbol-month is cached as a compressed ``.npz`` under the cache directory
  (default ``.frc_cache/``, git-ignored).
* ``LocalBars``: your own files, one per symbol, with a datetime index or an
  ``open_time`` column and ``open, high, low, close`` columns (CSV, Parquet, or
  pickle when explicitly allowed: only unpickle files you created yourself).

Symbols are validated before any path or URL is built (``validate_symbol``): only
``^[A-Z0-9]{2,20}$`` is accepted (optionally restricted further by an allow-list), Windows
reserved device names are refused, and every cache or local file path is resolved and
checked to stay inside its base directory.

Every loader returns a DataFrame indexed by tz-aware UTC bar OPEN time with float
columns ``open, high, low, close``, sorted and de-duplicated.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

ARCHIVE = "https://data.binance.vision/data"
REST = {"spot": "https://api.binance.com/api/v3/klines", "um": "https://fapi.binance.com/fapi/v1/klines"}
ARCHIVE_PATH = {"spot": "spot", "um": "futures/um"}
COLS = ["open", "high", "low", "close"]
USER_AGENT = "fill-reality-check/0.1 (+public market data)"
SYMBOL_RE = re.compile(r"[A-Z0-9]{2,20}")
# Windows device names: refused as file names even with an extension (CON.npz, COM1-...)
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}


class InvalidSymbolError(ValueError):
    """A symbol that is not a plain exchange symbol (refused before any path or URL is built)."""


def validate_symbol(symbol, allowed: Optional[Iterable[str]] = None) -> str:
    """Return ``symbol`` if it is a plain symbol such as ``BTCUSDT``; raise ``InvalidSymbolError``
    otherwise. ``allowed``, when given, is an allow-list of accepted symbols."""
    if not isinstance(symbol, str) or not SYMBOL_RE.fullmatch(symbol):
        shown = repr(symbol)[:40]
        raise InvalidSymbolError(f"invalid symbol {shown}: expected 2 to 20 characters A-Z or 0-9 (for example BTCUSDT)")
    if symbol in WINDOWS_RESERVED:
        raise InvalidSymbolError(f"invalid symbol {symbol!r}: reserved device name")
    if allowed is not None and symbol not in set(allowed):
        raise InvalidSymbolError(f"symbol {symbol!r} is not in the list of supported symbols")
    return symbol


def safe_join(base: Path, *parts: str) -> Path:
    """Join ``parts`` under ``base`` and check that the resolved path stays inside ``base``."""
    for part in parts:
        if not part or part in (".", "..") or ".." in part or any(c in part for c in "/\\:\x00"):
            raise InvalidSymbolError(f"unsafe path component {part[:40]!r}")
        if part.split(".")[0].split("-")[0].upper() in WINDOWS_RESERVED:
            raise InvalidSymbolError(f"unsafe path component {part[:40]!r}: reserved device name")
    root = Path(base).resolve()
    path = root.joinpath(*parts).resolve()
    if path != root and root not in path.parents:
        raise InvalidSymbolError(f"path escapes {root}: {path}")
    return path


def _to_frame(ms: np.ndarray, ohlc: np.ndarray) -> pd.DataFrame:
    idx = pd.to_datetime(ms.astype("int64"), unit="ms", utc=True)
    df = pd.DataFrame(ohlc.astype(float), index=idx, columns=COLS)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    df.index.name = "open_time"
    return df


def _parse_kline_csv(raw: bytes) -> tuple[np.ndarray, np.ndarray]:
    """Binance kline CSV (spot files have no header, futures files do; timestamps
    are ms, or us for spot files from 2025 on)."""
    df = pd.read_csv(io.BytesIO(raw), header=None, usecols=range(5), dtype=str)
    df = df[pd.to_numeric(df[0], errors="coerce").notna()]  # drop a header row if present
    t = df[0].astype("int64").to_numpy()
    t = np.where(t > 10 ** 14, t // 1000, t)  # microseconds -> milliseconds
    return t, df[[1, 2, 3, 4]].astype(float).to_numpy()


class BinanceArchive:
    """Download-and-cache loader for public Binance 1m klines."""

    def __init__(self, market: str = "spot", cache_dir: str | Path = ".frc_cache",
                 offline: bool = False, verbose: bool = True, allowed_symbols: Optional[Iterable[str]] = None):
        if market not in ARCHIVE_PATH:
            raise ValueError(f"market must be one of {sorted(ARCHIVE_PATH)}")
        self.market, self.offline, self.verbose = market, offline, verbose
        self.cache = Path(cache_dir) / f"binance_{market}_1m"
        self.allowed_symbols = None if allowed_symbols is None else frozenset(allowed_symbols)

    # -- network helpers ------------------------------------------------------------------
    def _get(self, url: str, retries: int = 3) -> Optional[bytes]:
        if self.offline:
            raise RuntimeError(f"offline mode and not cached: {url}")
        for k in range(retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if k == retries - 1:
                    raise
            except urllib.error.URLError:
                if k == retries - 1:
                    raise
            time.sleep(1.5 * (k + 1))
        return None

    def _archive(self, symbol: str, kind: str, stamp: str) -> Optional[tuple[np.ndarray, np.ndarray]]:
        validate_symbol(symbol)
        if kind not in ("monthly", "daily") or not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", stamp):
            raise ValueError(f"bad archive request {kind!r} {stamp!r}")
        name = f"{symbol}-1m-{stamp}.zip"
        q = urllib.parse.quote
        url = f"{ARCHIVE}/{ARCHIVE_PATH[self.market]}/{kind}/klines/{q(symbol, safe='')}/1m/{q(name, safe='')}"
        blob = self._get(url)
        if blob is None:
            return None
        chk = self._get(url + ".CHECKSUM")
        if chk:
            want = chk.decode().split()[0].lower()
            got = hashlib.sha256(blob).hexdigest()
            if want != got:
                raise ValueError(f"checksum mismatch for {name}")
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            raw = z.read(z.namelist()[0])
        return _parse_kline_csv(raw)

    def _rest(self, symbol: str, start_ms: int, end_ms: int) -> tuple[np.ndarray, np.ndarray]:
        ts, rows = [], []
        cur = start_ms
        while cur < end_ms:
            params = {"symbol": validate_symbol(symbol), "interval": "1m", "startTime": int(cur),
                      "endTime": int(end_ms) - 1, "limit": 1000}
            url = f"{REST[self.market]}?{urllib.parse.urlencode(params)}"
            blob = self._get(url)
            data = json.loads(blob) if blob else []
            if not data:
                break
            for k in data:
                ts.append(int(k[0]))
                rows.append([float(k[1]), float(k[2]), float(k[3]), float(k[4])])
            cur = int(data[-1][0]) + 60_000
            time.sleep(0.2)
        return np.asarray(ts, dtype="int64"), np.asarray(rows, dtype=float).reshape(-1, 4)

    # -- month cache ------------------------------------------------------------------
    def _month(self, symbol: str, month: pd.Period) -> pd.DataFrame:
        validate_symbol(symbol, self.allowed_symbols)
        path = safe_join(self.cache, symbol, f"{symbol}-{month}.npz")
        now = pd.Timestamp.now(tz="UTC")
        complete = month.end_time.tz_localize("UTC") < now - pd.Timedelta(days=2)
        if path.exists():
            z = np.load(path)
            if bool(z["complete"]) or not complete:
                return _to_frame(z["ms"], z["ohlc"])
        if self.offline:
            raise RuntimeError(f"offline mode and {symbol} {month} is not cached in {self.cache}")
        if self.verbose:
            print(f"  downloading {self.market} {symbol} {month} ...", flush=True)
        got = self._archive(symbol, "monthly", str(month))
        if got is None:  # month not archived yet: daily files, then REST for the tail
            parts = []
            day = month.start_time.tz_localize("UTC")
            end = min(month.end_time.tz_localize("UTC"), now)
            while day <= end:
                d = self._archive(symbol, "daily", day.strftime("%Y-%m-%d"))
                if d is None:
                    s_ms = int(day.value // 1_000_000)
                    e_ms = int(min(day + pd.Timedelta(days=1), now).value // 1_000_000)
                    d = self._rest(symbol, s_ms, e_ms)
                if len(d[0]):
                    parts.append(d)
                day += pd.Timedelta(days=1)
            got = (np.concatenate([p[0] for p in parts]) if parts else np.zeros(0, "int64"),
                   np.concatenate([p[1] for p in parts]) if parts else np.zeros((0, 4)))
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, ms=got[0], ohlc=got[1], complete=np.array(complete))
        return _to_frame(*got)

    def load(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        validate_symbol(symbol, self.allowed_symbols)
        months = pd.period_range(start.tz_convert(None).to_period("M"), end.tz_convert(None).to_period("M"), freq="M")
        frames = [self._month(symbol, m) for m in months]
        frames = [f for f in frames if len(f)]
        if not frames:
            return pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([], tz="UTC", name="open_time"))
        df = pd.concat(frames)
        df = df[~df.index.duplicated(keep="first")].sort_index()
        return df.loc[start:end]


class LocalBars:
    """Bars from a local directory, one file per symbol (``pattern`` formats ``{symbol}``)."""

    def __init__(self, directory: str | Path, pattern: str = "{symbol}.csv", allow_pickle: bool = False,
                 allowed_symbols: Optional[Iterable[str]] = None):
        self.dir, self.pattern, self.allow_pickle = Path(directory), pattern, allow_pickle
        self.allowed_symbols = None if allowed_symbols is None else frozenset(allowed_symbols)
        self._last: tuple[str, pd.DataFrame] | None = None  # one symbol in memory at a time

    def load(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        validate_symbol(symbol, self.allowed_symbols)
        if self._last is None or self._last[0] != symbol:
            self._last = None
            self._last = (symbol, self._read(symbol))
        return self._last[1].loc[start:end]

    def _read(self, symbol: str) -> pd.DataFrame:
        name = self.pattern.format(symbol=validate_symbol(symbol, self.allowed_symbols))
        # the pattern is the user's own (may contain sub-directories); the symbol is not
        root = self.dir.resolve()
        path = (self.dir / name).resolve()
        if root not in path.parents:
            raise InvalidSymbolError(f"bar file for {symbol} resolves outside {root}: {path}")
        if not path.exists():
            raise FileNotFoundError(f"no bars for {symbol}: {path}")
        suf = path.suffix.lower()
        if suf in (".pkl", ".pickle"):
            if not self.allow_pickle:
                raise ValueError("refusing to unpickle; pass allow_pickle=True (--allow-pickle) for your own files only")
            df = pd.read_pickle(path)
        elif suf == ".parquet":
            df = pd.read_parquet(path)
        else:
            df = pd.read_csv(path)
        if not isinstance(df.index, pd.DatetimeIndex):
            col = next((k for k in ("open_time", "timestamp", "time", "date") if k in df.columns), None)
            if col is None:
                raise ValueError(f"{path}: needs a datetime index or an open_time/timestamp column")
            v = df[col]
            if pd.api.types.is_numeric_dtype(v):
                v = np.where(v > 10 ** 14, v // 1000, v)
                df.index = pd.to_datetime(v, unit="ms", utc=True)
            else:
                df.index = pd.to_datetime(v, utc=True)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = df[COLS].astype(float)
        df = df[~df.index.duplicated(keep="first")].sort_index()
        return df


def symbols_needed(trades: pd.DataFrame) -> Iterable[str]:
    return sorted(trades.symbol.unique())
