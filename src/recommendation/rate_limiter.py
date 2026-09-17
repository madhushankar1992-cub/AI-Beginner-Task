"""In-process guard against Groq's per-account rate/token limits.

Tracks a local sliding window of recent calls so engine.py can skip a Groq
call before making it — and fall back to the sorted-by-rating response —
instead of waiting on or retrying a real 429. Blocking/retrying would work
against architecture.md's sub-10s interactive latency goal; an immediate,
usable fallback is the same contract Phase 4 already uses for any other
LLM failure.

Single-process, in-memory state (matches store.py's module-level singleton
pattern). This does not coordinate across multiple worker processes — if
this app is ever deployed with more than one worker, the limits need to
move to a shared store (e.g. Redis) instead.
"""

import time
from collections import deque

from src import config

# Rough token estimate for the pre-flight check: JSON-heavy prompts tokenize
# less efficiently than prose, so this errs conservative (fewer chars/token)
# rather than risk under-estimating and tripping a real limit.
CHARS_PER_TOKEN_ESTIMATE = 3.0
# Reserve for the completion side, based on measured usage (~570-1100
# completion tokens for a top-5 response at medium/low reasoning effort).
ESTIMATED_COMPLETION_TOKENS = 1_200

_MINUTE_SECONDS = 60.0
_DAY_SECONDS = 86_400.0


class RateLimitExceeded(Exception):
    """Raised when calling Groq now would likely breach a known account limit."""


class GroqRateLimiter:
    def __init__(self) -> None:
        self._calls: deque[tuple[float, int]] = deque()  # (timestamp, tokens)

    def _prune(self, now: float) -> None:
        while self._calls and now - self._calls[0][0] > _DAY_SECONDS:
            self._calls.popleft()

    def estimate_tokens(self, *texts: str) -> int:
        chars = sum(len(t) for t in texts)
        return int(chars / CHARS_PER_TOKEN_ESTIMATE) + ESTIMATED_COMPLETION_TOKENS

    def check(self, estimated_tokens: int, now: float | None = None) -> None:
        """Raise RateLimitExceeded if a call now would likely breach a limit."""
        now = time.time() if now is None else now
        self._prune(now)

        minute_calls = [c for c in self._calls if now - c[0] <= _MINUTE_SECONDS]
        day_calls = self._calls

        if len(minute_calls) >= config.GROQ_RPM_LIMIT:
            raise RateLimitExceeded(
                f"RPM guard: {len(minute_calls)}/{config.GROQ_RPM_LIMIT} requests in the last minute"
            )
        if len(day_calls) >= config.GROQ_RPD_LIMIT:
            raise RateLimitExceeded(f"RPD guard: {len(day_calls)}/{config.GROQ_RPD_LIMIT} requests today")

        minute_tokens = sum(c[1] for c in minute_calls)
        if minute_tokens + estimated_tokens > config.GROQ_TPM_LIMIT:
            raise RateLimitExceeded(
                f"TPM guard: {minute_tokens} used + ~{estimated_tokens} estimated "
                f"> {config.GROQ_TPM_LIMIT}/min"
            )

        day_tokens = sum(c[1] for c in day_calls)
        if day_tokens + estimated_tokens > config.GROQ_TPD_LIMIT:
            raise RateLimitExceeded(
                f"TPD guard: {day_tokens} used + ~{estimated_tokens} estimated "
                f"> {config.GROQ_TPD_LIMIT}/day"
            )

    def record(self, actual_tokens: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._calls.append((now, actual_tokens))


_limiter: GroqRateLimiter | None = None


def get_rate_limiter() -> GroqRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = GroqRateLimiter()
    return _limiter
