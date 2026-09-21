from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api import main
from src.api.models import RecommendationItem, RecommendationResponse
from src.recommendation.engine import EngineError


@pytest.fixture
def sample_store() -> pd.DataFrame:
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
                "name": "Budget Diner",
                "location": "Koramangala",
                "cuisine": ["south indian"],
                "cost_for_two": 200.0,
                "rating": 3.5,
                "tags": ["quick bites"],
                "budget_tier": "low",
            },
        ]
    )


@pytest.fixture
def client(sample_store, monkeypatch):
    # Bypasses the real parquet-backed store singleton so these tests have no
    # filesystem dependency, matching the isolation approach in test_filters.py.
    monkeypatch.setattr(main, "get_store", lambda: sample_store)
    return TestClient(main.app)


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_empty_location_is_rejected(client):
    resp = client.post("/recommendations", json={"location": ""})
    assert resp.status_code == 422


def test_whitespace_location_is_rejected(client):
    resp = client.post("/recommendations", json={"location": "   "})
    assert resp.status_code == 422


def test_missing_location_field_is_rejected(client):
    resp = client.post("/recommendations", json={})
    assert resp.status_code == 422


def test_malformed_cuisine_type_is_rejected(client):
    resp = client.post("/recommendations", json={"location": "Banashankari", "cuisine": "not-a-list"})
    assert resp.status_code == 422


def test_malformed_min_rating_type_is_rejected(client):
    resp = client.post("/recommendations", json={"location": "Banashankari", "min_rating": "not-a-number"})
    assert resp.status_code == 422


def test_min_rating_out_of_range_is_clamped_not_rejected(client, monkeypatch):
    ai_response = RecommendationResponse(recommendations=[], summary="unused", source="ai")
    monkeypatch.setattr(main, "generate_recommendations", lambda candidates, preferences, location: ai_response)

    resp = client.post("/recommendations", json={"location": "Banashankari", "min_rating": 10})
    assert resp.status_code == 200


def test_unknown_location_returns_empty_not_500(client):
    resp = client.post("/recommendations", json={"location": "Nowhereville"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommendations"] == []
    assert body["source"] == "fallback"


def test_unknown_location_skips_llm_call_entirely(client, monkeypatch):
    mock_generate = MagicMock()
    monkeypatch.setattr(main, "generate_recommendations", mock_generate)

    resp = client.post("/recommendations", json={"location": "Nowhereville"})

    assert resp.status_code == 200
    mock_generate.assert_not_called()


def test_known_location_returns_ai_response_on_success(client, monkeypatch):
    ai_response = RecommendationResponse(
        recommendations=[
            RecommendationItem(
                name="Jalsa",
                cuisine="north indian, chinese",
                rating=4.1,
                estimated_cost="₹800 for two",
                explanation="Great match.",
            )
        ],
        summary="Top pick.",
        source="ai",
    )
    monkeypatch.setattr(main, "generate_recommendations", lambda candidates, preferences, location: ai_response)

    resp = client.post("/recommendations", json={"location": "Banashankari"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "ai"
    assert body["recommendations"][0]["name"] == "Jalsa"


def test_engine_failure_falls_back_to_sorted_by_rating(client, monkeypatch):
    def raise_engine_error(candidates, preferences, location):
        raise EngineError("simulated failure")

    monkeypatch.setattr(main, "generate_recommendations", raise_engine_error)

    resp = client.post("/recommendations", json={"location": "Banashankari"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "fallback"
    assert len(body["recommendations"]) == 1
    assert body["recommendations"][0]["name"] == "Jalsa"
    assert "sorted by rating" in body["summary"].lower()
