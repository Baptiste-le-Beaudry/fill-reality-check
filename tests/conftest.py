import pandas as pd


class DictBars:
    """In-memory loader used by the tests (no network, no files)."""

    def __init__(self, frames):
        self.frames = frames

    def load(self, symbol, start, end):
        if symbol not in self.frames:
            raise FileNotFoundError(symbol)
        return self.frames[symbol].loc[start:end]


def make_bars(rows, start="2024-03-01 00:00"):
    """rows: list of (open, high, low, close)."""
    idx = pd.date_range(start, periods=len(rows), freq="min", tz="UTC")
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"], dtype=float)
