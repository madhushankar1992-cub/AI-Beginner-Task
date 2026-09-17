"""Request/response contract for POST /recommendations, per architecture.md §4.3-4.5."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class RecommendationRequest(BaseModel):
    location: str
    budget: str | None = None
    cuisine: list[str] = Field(default_factory=list)
    min_rating: float = 0.0
    preferences: list[str] = Field(default_factory=list)

    @field_validator("location")
    @classmethod
    def location_must_not_be_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("location must not be empty")
        return v.strip()

    @field_validator("min_rating")
    @classmethod
    def clamp_min_rating(cls, v: float) -> float:
        return max(0.0, min(5.0, v))


class RecommendationItem(BaseModel):
    name: str
    # architecture.md's JSON contract represents cuisine as a single string per
    # item (unlike the store's list column), so multi-cuisine rows are joined.
    cuisine: str
    rating: float
    estimated_cost: str
    explanation: str


class RecommendationResponse(BaseModel):
    recommendations: list[RecommendationItem]
    summary: str
    # "fallback" = sorted-by-rating stub/degraded response; "ai" = real LLM
    # ranking (Phase 4). Established now so Phase 4/5 can label results
    # correctly per architecture.md §4.6 without changing the response shape.
    source: Literal["ai", "fallback"] = "fallback"
