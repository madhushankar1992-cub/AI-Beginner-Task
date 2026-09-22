import pandas as pd
import pytest

from src.recommendation.filters import filter_restaurants


@pytest.fixture
def store() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "name": "Jalsa",
                "location": "Banashankari",
                "cuisine": ["north indian", "chinese"],
                "cost_for_two": 800.0,
                "rating": 4.1,
                "tags": ["casual dining"],
                "budget_tier": "high",
            },
            {
                "name": "Spice Elephant",
                "location": "Banashankari",
                "cuisine": ["chinese", "thai"],
                "cost_for_two": 800.0,
                "rating": 4.1,
                "tags": ["casual dining"],
                "budget_tier": "high",
            },
            {
                "name": "Cheap Eats",
                "location": "Koramangala",
                "cuisine": ["south indian"],
                "cost_for_two": 200.0,
                "rating": 3.5,
                "tags": ["quick bites"],
                "budget_tier": "low",
            },
            {
                "name": "Budget Diner",
                "location": "Koramangala",
                "cuisine": ["south indian", "north indian"],
                "cost_for_two": 250.0,
                "rating": 4.0,
                "tags": ["quick bites"],
                "budget_tier": "low",
            },
        ]
    )


def test_exact_match(store):
    result = filter_restaurants(store, location="Banashankari", cuisine=["chinese"], min_rating=4.0)
    assert set(result["name"]) == {"Jalsa", "Spice Elephant"}


def test_location_match_is_case_insensitive(store):
    result = filter_restaurants(store, location="banashankari")
    assert len(result) == 2


def test_no_match_returns_empty_dataframe_not_error(store):
    result = filter_restaurants(store, location="Nonexistent City")
    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_unmatched_location_with_cuisine_filter_returns_empty_dataframe(store):
    # Regression: an empty intermediate frame plus a cuisine filter used to
    # drop every column and raise KeyError: 'rating'.
    result = filter_restaurants(store, location="Nonexistent City", cuisine=["chinese"], min_rating=3.0)
    assert isinstance(result, pd.DataFrame)
    assert result.empty
    assert "name" in result.columns


def test_partial_cuisine_any_match(store):
    # "thai" matches only Spice Elephant; "italian" matches nobody in the store.
    result = filter_restaurants(store, cuisine=["thai", "italian"])
    assert set(result["name"]) == {"Spice Elephant"}


def test_rating_boundary_is_inclusive(store):
    result = filter_restaurants(store, min_rating=4.0)
    assert set(result["name"]) == {"Jalsa", "Spice Elephant", "Budget Diner"}
    assert "Cheap Eats" not in set(result["name"])


def test_budget_tier_filter(store):
    result = filter_restaurants(store, budget="low")
    assert set(result["name"]) == {"Cheap Eats", "Budget Diner"}


def test_cap_enforcement(store):
    result = filter_restaurants(store, cap=2)
    assert len(result) == 2
    # top 2 by rating: the two 4.1s (Jalsa, Spice Elephant) beat Budget Diner (4.0).
    assert set(result["name"]) == {"Jalsa", "Spice Elephant"}


def test_no_filters_returns_all_rows_capped(store):
    result = filter_restaurants(store)
    assert len(result) == len(store)


def test_empty_cuisine_list_means_no_cuisine_filter(store):
    result = filter_restaurants(store, cuisine=[])
    assert len(result) == len(store)
