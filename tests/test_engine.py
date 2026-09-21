import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src import config
from src.recommendation import engine
from src.recommendation.engine import EngineError
from src.recommendation.rate_limiter import GroqRateLimiter


@pytest.fixture
def candidates() -> pd.DataFrame:
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
        ]
    )


def make_completion(payload: dict, total_tokens: int | None = 500):
    completion = MagicMock()
    completion.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    completion.usage = None if total_tokens is None else MagicMock(total_tokens=total_tokens)
    return completion


@pytest.fixture(autouse=True)
def isolated_rate_limiter(monkeypatch):
    """Every test gets a fresh, empty rate limiter so tests can't interfere
    with each other (or with real usage) through the module-level singleton."""
    monkeypatch.setattr(engine, "get_rate_limiter", lambda: GroqRateLimiter())


def test_generate_recommendations_success(candidates, monkeypatch):
    mock_client = MagicMock()
    payload = {
        "recommendations": [
            {
                "name": "Jalsa",
                "cuisine": "north indian, chinese",
                "rating": 4.1,
                "estimated_cost": "₹800 for two",
                "explanation": "Highly rated and matches the cuisine preference.",
            }
        ],
        "summary": "Top pick for north indian food.",
    }
    mock_client.chat.completions.create.return_value = make_completion(payload)
    monkeypatch.setattr(engine, "get_client", lambda: mock_client)

    result = engine.generate_recommendations(candidates, ["family-friendly"], "Banashankari")

    assert result.source == "ai"
    assert len(result.recommendations) == 1
    assert result.recommendations[0].name == "Jalsa"
    assert result.summary == "Top pick for north indian food."


def test_prompt_and_model_params_are_passed_correctly(candidates, monkeypatch):
    mock_client = MagicMock()
    payload = {
        "recommendations": [
            {"name": "Jalsa", "cuisine": "x", "rating": 4.1, "estimated_cost": "y", "explanation": "z"}
        ],
        "summary": "s",
    }
    mock_client.chat.completions.create.return_value = make_completion(payload)
    monkeypatch.setattr(engine, "get_client", lambda: mock_client)

    engine.generate_recommendations(candidates, ["quick service"], "Banashankari")

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["model"] == config.MODEL_NAME
    assert kwargs["reasoning_effort"] == "medium"
    assert kwargs["response_format"] == {
        "type": "json_schema",
        "json_schema": engine.RESPONSE_JSON_SCHEMA,
    }

    messages = kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert "only recommend restaurants from the provided candidate list" in messages[0]["content"].lower()
    assert messages[1]["role"] == "user"
    assert "quick service" in messages[1]["content"]
    assert "Jalsa" in messages[1]["content"]


def test_invalid_json_raises_engine_error(candidates, monkeypatch):
    mock_client = MagicMock()
    completion = MagicMock()
    completion.choices = [MagicMock(message=MagicMock(content="not valid json"))]
    completion.usage = MagicMock(total_tokens=100)
    mock_client.chat.completions.create.return_value = completion
    monkeypatch.setattr(engine, "get_client", lambda: mock_client)

    with pytest.raises(EngineError, match="not valid JSON"):
        engine.generate_recommendations(candidates, [], "Banashankari")


def test_schema_mismatch_raises_engine_error(candidates, monkeypatch):
    mock_client = MagicMock()
    # Missing the required "rating" field triggers a pydantic ValidationError.
    payload = {
        "recommendations": [
            {"name": "Jalsa", "cuisine": "north indian", "estimated_cost": "₹800", "explanation": "good"}
        ],
        "summary": "s",
    }
    mock_client.chat.completions.create.return_value = make_completion(payload)
    monkeypatch.setattr(engine, "get_client", lambda: mock_client)

    with pytest.raises(EngineError, match="did not match expected schema"):
        engine.generate_recommendations(candidates, [], "Banashankari")


def test_hallucinated_restaurant_is_dropped_but_grounded_ones_survive(candidates, monkeypatch):
    mock_client = MagicMock()
    payload = {
        "recommendations": [
            {"name": "Jalsa", "cuisine": "north indian", "rating": 4.1, "estimated_cost": "₹800", "explanation": "real"},
            {"name": "Totally Made Up Place", "cuisine": "italian", "rating": 5.0, "estimated_cost": "₹1", "explanation": "fake"},
        ],
        "summary": "s",
    }
    mock_client.chat.completions.create.return_value = make_completion(payload)
    monkeypatch.setattr(engine, "get_client", lambda: mock_client)

    result = engine.generate_recommendations(candidates, [], "Banashankari")

    assert {r.name for r in result.recommendations} == {"Jalsa"}


def test_all_hallucinated_raises_engine_error(candidates, monkeypatch):
    mock_client = MagicMock()
    payload = {
        "recommendations": [
            {"name": "Totally Made Up Place", "cuisine": "italian", "rating": 5.0, "estimated_cost": "₹1", "explanation": "fake"},
        ],
        "summary": "s",
    }
    mock_client.chat.completions.create.return_value = make_completion(payload)
    monkeypatch.setattr(engine, "get_client", lambda: mock_client)

    with pytest.raises(EngineError, match="no grounded recommendations"):
        engine.generate_recommendations(candidates, [], "Banashankari")


def test_api_error_raises_engine_error(candidates, monkeypatch):
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = TimeoutError("simulated timeout")
    monkeypatch.setattr(engine, "get_client", lambda: mock_client)

    with pytest.raises(EngineError, match="Groq API call failed"):
        engine.generate_recommendations(candidates, [], "Banashankari")


def test_missing_api_key_raises_engine_error(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(engine, "_client", None)

    with pytest.raises(EngineError, match="GROQ_API_KEY"):
        engine.get_client()


def test_rate_limit_guard_blocks_call_before_hitting_groq(candidates, monkeypatch):
    mock_client = MagicMock()
    monkeypatch.setattr(engine, "get_client", lambda: mock_client)

    tripped_limiter = GroqRateLimiter()
    tripped_limiter.record(actual_tokens=config.GROQ_TPM_LIMIT)  # already at the cap
    monkeypatch.setattr(engine, "get_rate_limiter", lambda: tripped_limiter)

    with pytest.raises(EngineError, match="rate/token guard tripped"):
        engine.generate_recommendations(candidates, [], "Banashankari")

    mock_client.chat.completions.create.assert_not_called()


def test_usage_is_recorded_against_the_rate_limiter(candidates, monkeypatch):
    mock_client = MagicMock()
    payload = {
        "recommendations": [
            {"name": "Jalsa", "cuisine": "x", "rating": 4.1, "estimated_cost": "y", "explanation": "z"}
        ],
        "summary": "s",
    }
    mock_client.chat.completions.create.return_value = make_completion(payload, total_tokens=1234)
    monkeypatch.setattr(engine, "get_client", lambda: mock_client)

    limiter = GroqRateLimiter()
    monkeypatch.setattr(engine, "get_rate_limiter", lambda: limiter)

    engine.generate_recommendations(candidates, [], "Banashankari")

    assert limiter._calls[-1][1] == 1234
