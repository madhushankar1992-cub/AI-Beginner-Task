"""Deterministic, LLM-free filtering: turns user preferences into a candidate set."""

import pandas as pd

DEFAULT_CAP = 30


def filter_restaurants(
    store: pd.DataFrame,
    location: str | None = None,
    budget: str | None = None,
    cuisine: list[str] | None = None,
    min_rating: float = 0.0,
    cap: int = DEFAULT_CAP,
) -> pd.DataFrame:
    df = store

    if location and location.strip():
        target = location.strip().lower()
        df = df[df["location"].str.lower() == target]

    if budget and budget.strip():
        target = budget.strip().lower()
        df = df[df["budget_tier"].astype(str).str.lower() == target]

    if cuisine:
        wanted = {c.strip().lower() for c in cuisine if c and c.strip()}
        if wanted:
            # astype(bool): .apply() on an already-empty frame returns an empty
            # object-dtype Series, which pandas would treat as a column selector
            # (dropping every column) instead of a boolean mask.
            has_wanted_cuisine = df["cuisine"].apply(
                lambda row_cuisines: bool(wanted & {str(c).lower() for c in row_cuisines})
            ).astype(bool)
            df = df[has_wanted_cuisine]

    df = df[df["rating"] >= min_rating]

    # kind="stable" keeps tie-breaking among equal ratings deterministic (same
    # input store always caps to the same rows, not an arbitrary sort order).
    df = df.sort_values("rating", ascending=False, kind="stable").head(cap)

    return df.reset_index(drop=True)
