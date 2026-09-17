"""System and user prompt construction for the recommendation engine, per architecture.md §4.5."""

import json

import pandas as pd

SYSTEM_PROMPT = """You are a restaurant recommendation assistant for a Zomato-style app.

You will be given a JSON list of candidate restaurants — these are the ONLY
restaurants that exist for this request — plus the user's preferences. Rank
the best matches and explain each pick.

Rules:
- Only recommend restaurants from the provided candidate list. Never invent a
  restaurant, or a fact about one (cuisine, rating, cost), that isn't present
  in the list.
- Weigh rating, budget fit, cuisine match, and the user's free-text
  preferences (matched against each restaurant's tags where possible) when
  ranking.
- Write a short, specific explanation per pick that references concrete
  details (cuisine, cost, rating, or a matched preference/tag) rather than
  generic filler that could apply to any restaurant.
- If a preference has no clear support in the data, don't fabricate a
  connection — rely on the criteria that are actually grounded.
- If fewer candidates are provided than requested, return only what's
  available; never pad the list with invented restaurants.
- Respond with JSON matching the required schema only, with no extra text."""


def build_system_prompt() -> str:
    return SYSTEM_PROMPT


def build_user_message(
    candidates: pd.DataFrame,
    preferences: list[str],
    location: str,
    top_k: int,
) -> str:
    candidate_payload = [
        {
            "name": row["name"],
            "cuisine": list(row["cuisine"]),
            "rating": float(row["rating"]),
            "cost_for_two": float(row["cost_for_two"]),
            "budget_tier": str(row["budget_tier"]),
            "tags": list(row["tags"]),
        }
        for _, row in candidates.iterrows()
    ]

    request = {
        "location": location,
        "preferences": preferences,
        "top_k": top_k,
        "candidates": candidate_payload,
    }
    return (
        f"Return the top {top_k} recommendations (fewer if the candidate list "
        f"is smaller than {top_k}) as JSON.\n\nRequest:\n"
        f"{json.dumps(request, ensure_ascii=False)}"
    )
