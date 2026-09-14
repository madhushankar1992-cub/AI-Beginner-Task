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
            df = df[
                df["cuisine"].apply(
                    lambda row_cuisines: bool(wanted & {str(c).lower() for c in row_cuisines})
                )
            ]

    df = df[df["rating"] >= min_rating]

    # kind="stable" keeps tie-breaking among equal ratings deterministic (same
    # input store always caps to the same rows, not an arbitrary sort order).
    df = df.sort_values("rating", ascending=False, kind="stable").head(cap)

    return df.reset_index(drop=True)
