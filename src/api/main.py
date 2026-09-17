import logging

import pandas as pd
from fastapi import FastAPI

from src.api.models import RecommendationItem, RecommendationRequest, RecommendationResponse
from src.api.store import get_store
from src.recommendation.engine import EngineError, generate_recommendations
from src.recommendation.filters import filter_restaurants

logger = logging.getLogger(__name__)

app = FastAPI()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _fallback_response(candidates: pd.DataFrame, location: str) -> RecommendationResponse:
    items = [
        RecommendationItem(
            name=row["name"],
            cuisine=", ".join(row["cuisine"]),
            rating=float(row["rating"]),
            estimated_cost=f"₹{int(row['cost_for_two'])} for two",
            explanation="Sorted by rating (AI ranking unavailable).",
        )
        for _, row in candidates.iterrows()
    ]
    return RecommendationResponse(
        recommendations=items,
        summary=f"Top {len(items)} restaurants in {location}, sorted by rating.",
        source="fallback",
    )


@app.post("/recommendations", response_model=RecommendationResponse)
def recommendations(request: RecommendationRequest) -> RecommendationResponse:
    store = get_store()
    candidates = filter_restaurants(
        store,
        location=request.location,
        budget=request.budget,
        cuisine=request.cuisine,
        min_rating=request.min_rating,
    )

    if candidates.empty:
        return RecommendationResponse(
            recommendations=[],
            summary=f"No restaurants matched your filters in {request.location}. Try relaxing them.",
        )

    try:
        return generate_recommendations(candidates, request.preferences, request.location)
    except EngineError as exc:
        logger.warning("Falling back to sorted-by-rating response: %s", exc)
        return _fallback_response(candidates, request.location)
