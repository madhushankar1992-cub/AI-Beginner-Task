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
