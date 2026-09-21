"""Runs the fixed 10-query eval set from docs/eval.md against the real Groq
API and prints results for manual review (grounding, quality, token usage,
latency). Informal by design per Phase 6 / docs/eval.md §7 - not a pytest
gate, not run in CI. Requires a real GROQ_API_KEY in the environment.

Usage: python -m scripts.run_eval
"""

import json
import time

from src import config
from src.api.store import get_store
from src.recommendation.engine import get_client
from src.recommendation.filters import filter_restaurants
from src.recommendation.prompts import build_system_prompt, build_user_message
from src.recommendation.rate_limiter import RateLimitExceeded, get_rate_limiter

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

# Mirrors docs/eval.md §2's table, with placeholders replaced by real values
# confirmed present in the ingested dataset (see the location/cuisine value
# counts used to pick these).
QUERIES = [
    ("1. Common city + common cuisine, generous budget",
     dict(location="Whitefield", cuisine=["north indian"], budget="medium", min_rating=3.5), []),
    ("2. Narrow filter, small candidate set",
     dict(location="Malleshwaram", cuisine=["seafood"], min_rating=3.5), []),
    ("3. Multiple cuisines requested",
     dict(location="BTM", cuisine=["chinese", "italian"]), []),
    ("4. Free-text preference with a real tag/description signal",
     dict(location="HSR", min_rating=3.5), ["family-friendly"]),
    ("5. Free-text preference with no clear dataset signal",
     dict(location="HSR", min_rating=3.5), ["good for a first date"]),
    ("6. Budget-constrained",
     dict(location="HSR", budget="low", min_rating=4.0), []),
    ("7. High min_rating, likely small result set",
     dict(location="Whitefield", min_rating=4.7), []),
    ("8. Zero-match query",
     dict(location="Atlantis City Nowhere"), []),
    ("9. No cuisine/preference filters, location only",
     dict(location="HSR"), []),
    ("10. Repeat of #1 (run-to-run consistency check)",
     dict(location="Whitefield", cuisine=["north indian"], budget="medium", min_rating=3.5), []),
]


def run_query(label: str, filter_kwargs: dict, preferences: list[str]) -> None:
    print(f"\n{'=' * 70}\n{label}\nfilters={filter_kwargs} preferences={preferences}")

    store = get_store()
    candidates = filter_restaurants(store, **filter_kwargs)
    print(f"candidate set: {len(candidates)} restaurants")

    if candidates.empty:
        print("-> short-circuited: no LLM call (matches architecture.md §4.4)")
        return

    valid_names = set(candidates["name"])
    system_prompt = build_system_prompt()
    user_message = build_user_message(
        candidates, preferences, filter_kwargs.get("location", ""), top_k=config.TOP_K
    )

    limiter = get_rate_limiter()
    estimated = limiter.estimate_tokens(system_prompt, user_message)
    # Real full-size queries run ~3-4.5K tokens against an 8K TPM budget, so
    # back-to-back queries in this eval set can't all fire immediately.
    # Blocking (rather than failing over, as engine.py does for a live user
    # request) is the right call here - this is an offline manual script
    # where a short wait is fine and losing a query to the fallback path
    # would defeat the point of the eval run.
    for attempt in range(12):
        try:
            limiter.check(estimated)
            break
        except RateLimitExceeded as exc:
            print(f"  (local rate/token guard: {exc} - waiting 15s)")
            time.sleep(15)
    else:
        print("-> giving up on this query after repeated rate-limit waits")
        return

    client = get_client()
    start = time.monotonic()
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
    latency = time.monotonic() - start
    if completion.usage is not None:
        limiter.record(completion.usage.total_tokens)

    payload = json.loads(completion.choices[0].message.content)
    recs = payload.get("recommendations", [])
    grounded = [r for r in recs if r.get("name") in valid_names]

    print(f"latency: {latency:.2f}s | usage: {completion.usage}")
    print(f"returned: {len(recs)} | grounded: {len(grounded)} | hallucinated: {len(recs) - len(grounded)}")
    print(f"summary: {payload.get('summary', '')}")
    for r in recs:
        flag = "OK" if r.get("name") in valid_names else "!! NOT IN CANDIDATE SET !!"
        print(f"  [{flag}] {r.get('name')} (rating {r.get('rating')}): {r.get('explanation')}")


def main() -> None:
    for label, filter_kwargs, preferences in QUERIES:
        run_query(label, filter_kwargs, preferences)


if __name__ == "__main__":
    main()
