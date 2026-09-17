"""Calls Groq for ranked, explained recommendations.

Raises EngineError on any failure (API error, timeout, schema mismatch, or a
response with no grounded recommendations) so the API layer can fall back to
the Phase 3 sorted-by-rating stub per architecture.md §4.5-4.6.
"""

import json
import logging
import os

import pandas as pd
from groq import Groq
from pydantic import ValidationError

from src import config
from src.api.models import RecommendationItem, RecommendationResponse
from src.recommendation.prompts import build_system_prompt, build_user_message

logger = logging.getLogger(__name__)

RESPONSE_JSON_SCHEMA = {
    "name": "restaurant_recommendations",
    "schema": {
        "type": "object",
        "properties": {
            "recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "cuisine": {"type": "string"},
                        "rating": {"type": "number"},
                        "estimated_cost": {"type": "string"},
                        "explanation": {"type": "string"},
                    },
                    "required": ["name", "cuisine", "rating", "estimated_cost", "explanation"],
                    "additionalProperties": False,
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["recommendations", "summary"],
        "additionalProperties": False,
    },
    "strict": True,
}


class EngineError(Exception):
    """Raised when the LLM call fails or its output can't be trusted."""


_client: Groq | None = None


def get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise EngineError("GROQ_API_KEY is not set")
        _client = Groq(api_key=api_key)
    return _client


def generate_recommendations(
    candidates: pd.DataFrame,
    preferences: list[str],
    location: str,
) -> RecommendationResponse:
    client = get_client()
    system_prompt = build_system_prompt()
    user_message = build_user_message(candidates, preferences, location, top_k=config.TOP_K)

    try:
        completion = client.chat.completions.create(
            model=config.MODEL_NAME,
            reasoning_effort=config.REASONING_EFFORT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_schema", "json_schema": RESPONSE_JSON_SCHEMA},
            timeout=config.LLM_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise EngineError(f"Groq API call failed: {exc}") from exc

    raw_content = completion.choices[0].message.content
    try:
        payload = json.loads(raw_content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise EngineError(f"Groq response was not valid JSON: {exc}") from exc

    # Grounding guardrail: only trust picks that actually exist in the
    # filtered candidate set. The prompt instructs the model not to invent
    # restaurants, but this is enforced rather than assumed.
    valid_names = set(candidates["name"])
    try:
        items = [
            RecommendationItem(**rec)
            for rec in payload["recommendations"]
            if rec.get("name") in valid_names
        ]
    except (KeyError, TypeError, ValidationError) as exc:
        raise EngineError(f"Groq response did not match expected schema: {exc}") from exc

    if not items:
        raise EngineError("Groq returned no grounded recommendations")

    return RecommendationResponse(
        recommendations=items,
        summary=payload.get("summary", ""),
        source="ai",
    )
