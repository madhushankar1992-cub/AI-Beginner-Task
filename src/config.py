"""Central config: model name, reasoning effort, and thresholds for the LLM call.

MODEL_NAME is overridable via GROQ_MODEL so swapping to qwen/qwen3-32b
(architecture.md §4.5's documented alternative) is a one-line env change,
not a code change.
"""

import os

MODEL_NAME = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
REASONING_EFFORT = "medium"
TOP_K = 5
LLM_TIMEOUT_SECONDS = 10.0

# Groq's published per-account limits for openai/gpt-oss-120b. A measured
# full-size (~30 candidate) request runs ~3,200-4,300 total tokens, so TPM
# is the binding constraint here (only ~2 full calls/min before it trips),
# well before RPM would ever matter. src/recommendation/rate_limiter.py
# enforces these locally so a request that would exceed them falls back
# to the sorted-by-rating response instead of burning a real 429.
GROQ_RPM_LIMIT = 30
GROQ_RPD_LIMIT = 1_000
GROQ_TPM_LIMIT = 8_000
GROQ_TPD_LIMIT = 200_000

# Browser origins allowed to call the API (the static frontend in
# frontend/web is served from a different origin than the API).
CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ALLOW_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]
