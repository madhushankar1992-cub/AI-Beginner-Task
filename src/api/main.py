from fastapi import FastAPI

from src.api.models import RecommendationItem, RecommendationRequest, RecommendationResponse
from src.api.store import get_store
from src.recommendation.filters import filter_restaurants

app = FastAPI()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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

    items = [
        RecommendationItem(
            name=row["name"],
            cuisine=", ".join(row["cuisine"]),
            rating=float(row["rating"]),
            estimated_cost=f"₹{int(row['cost_for_two'])} for two",
            explanation="Sorted by rating (AI ranking not yet enabled).",
        )
        for _, row in candidates.iterrows()
    ]

    return RecommendationResponse(
        recommendations=items,
        summary=f"Top {len(items)} restaurants in {request.location}, sorted by rating.",
    )
