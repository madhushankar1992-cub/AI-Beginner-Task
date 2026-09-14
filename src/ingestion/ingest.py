"""Loads the raw Zomato dataset, cleans it, and writes data/restaurants.parquet.

Raw schema (confirmed via inspection, not the architecture.md guess):
  name, location, cuisines, approx_cost(for two people), rate, rest_type,
  dish_liked, votes, address, url, online_order, book_table, phone,
  reviews_list, menu_item, listed_in(type), listed_in(city)

Target schema: name, location, cuisine (list), cost_for_two, rating, tags (list), budget_tier.

Cleaning rules (see inline comments for why):
  - rate "4.1/5" / "4.1 /5" / "NEW" / null -> float rating or null.
  - approx_cost "1,200" -> float 1200.0.
  - cuisines "A, B, C" -> ["a", "b", "c"].
  - tags = normalized rest_type tokens + dish_liked tokens (supplementary, not required).
  - Rows are deduplicated on (name, address): the raw dataset lists the same physical
    restaurant once per listed_in(type)/listed_in(city) tag combination, so name+address
    duplicates are re-listings of the same place, not distinct restaurants.
  - Rows missing name/location/rating/cost_for_two/cuisine are dropped: these are the
    fields Phase 2 filters and matches on, so an unusable value there makes the row
    unfit for the product rather than partially usable.
  - budget_tier is 3 equal-count buckets over cost_for_two, computed via rank-based
    qcut so heavy clumping at round cost values (300, 400, 500, ...) can't collapse
    the split below 3 distinct tiers.
"""

from pathlib import Path

import pandas as pd
from datasets import load_dataset

DATASET_ID = "ManikaSaini/zomato-restaurant-recommendation"
OUTPUT_PATH = Path(__file__).resolve().parents[2] / "data" / "restaurants.parquet"


def load_raw() -> pd.DataFrame:
    ds = load_dataset(DATASET_ID)
    return ds["train"].to_pandas()


def parse_rating(value) -> float:
    if not isinstance(value, str):
        return float("nan")
    value = value.strip()
    if value in ("", "NEW", "-"):
        return float("nan")
    numeric = value.split("/")[0].strip()
    try:
        return float(numeric)
    except ValueError:
        return float("nan")


def parse_cost(value) -> float:
    if not isinstance(value, str):
        return float("nan")
    value = value.strip().replace(",", "")
    if value == "":
        return float("nan")
    try:
        return float(value)
    except ValueError:
        return float("nan")


def parse_cuisine_list(value) -> list:
    if not isinstance(value, str) or not value.strip():
        return []
    return [c.strip().lower() for c in value.split(",") if c.strip()]


def build_tags(rest_type, dish_liked) -> list:
    tokens = []
    if isinstance(rest_type, str):
        tokens += [t.strip().lower() for t in rest_type.split(",") if t.strip()]
    if isinstance(dish_liked, str):
        tokens += [t.strip().lower() for t in dish_liked.split(",") if t.strip()]
    # dedupe while preserving order
    seen = set()
    deduped = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped


def derive_budget_tier(cost: pd.Series) -> pd.Series:
    # rank(method="first") breaks ties deterministically so qcut always yields
    # exactly 3 equal-sized bins, regardless of duplicate cost values.
    ranked = cost.rank(method="first")
    return pd.qcut(ranked, 3, labels=["low", "medium", "high"])


def clean(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    report = {"rows_in": len(raw)}

    df = raw.copy()

    before_dedup = len(df)
    df = df.drop_duplicates(subset=["name", "address"], keep="first")
    report["dropped_duplicates"] = before_dedup - len(df)

    df["rating"] = df["rate"].apply(parse_rating)
    df["cost_for_two"] = df["approx_cost(for two people)"].apply(parse_cost)
    df["cuisine"] = df["cuisines"].apply(parse_cuisine_list)
    df["tags"] = [
        build_tags(rt, dl) for rt, dl in zip(df["rest_type"], df["dish_liked"])
    ]
    df["location"] = df["location"].astype("string").str.strip()
    df["name"] = df["name"].astype("string").str.strip()

    report["null_counts_after_parse"] = {
        "name": int(df["name"].isna().sum()),
        "location": int(df["location"].isna().sum()),
        "rating": int(df["rating"].isna().sum()),
        "cost_for_two": int(df["cost_for_two"].isna().sum()),
        "cuisine_empty": int((df["cuisine"].apply(len) == 0).sum()),
    }

    required_ok = (
        df["name"].notna()
        & (df["name"] != "")
        & df["location"].notna()
        & (df["location"] != "")
        & df["rating"].notna()
        & df["cost_for_two"].notna()
        & (df["cuisine"].apply(len) > 0)
    )
    before_required = len(df)
    df = df[required_ok].copy()
    report["dropped_missing_required_fields"] = before_required - len(df)

    df["budget_tier"] = derive_budget_tier(df["cost_for_two"])

    df = df[["name", "location", "cuisine", "cost_for_two", "rating", "tags", "budget_tier"]]
    df = df.reset_index(drop=True)

    report["rows_out"] = len(df)
    return df, report


def print_report(report: dict) -> None:
    print("=== Ingestion Report ===")
    print(f"Rows in (raw):              {report['rows_in']}")
    print(f"Dropped as duplicates:      {report['dropped_duplicates']}")
    print("Null/empty counts after parsing (pre-drop):")
    for field, count in report["null_counts_after_parse"].items():
        print(f"  {field}: {count}")
    print(f"Dropped missing required fields: {report['dropped_missing_required_fields']}")
    print(f"Rows out (cleaned):         {report['rows_out']}")


def main() -> None:
    raw = load_raw()
    cleaned, report = clean(raw)
    print_report(report)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    cleaned.to_parquet(OUTPUT_PATH, index=False)
    print(f"\nWrote {len(cleaned)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
