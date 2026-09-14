"""Loads restaurants.parquet into memory once and caches it (module-level singleton)."""

from pathlib import Path

import pandas as pd

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "restaurants.parquet"

_store: pd.DataFrame | None = None


def load_store(path: Path = DATA_PATH) -> pd.DataFrame:
    return pd.read_parquet(path)


def get_store() -> pd.DataFrame:
    global _store
    if _store is None:
        _store = load_store()
    return _store
