import pytest

from src import config
from src.recommendation.rate_limiter import GroqRateLimiter, RateLimitExceeded


def test_check_allows_calls_under_all_limits():
    limiter = GroqRateLimiter()
    limiter.check(estimated_tokens=100, now=1_000.0)  # should not raise


def test_check_raises_when_rpm_exceeded():
    limiter = GroqRateLimiter()
    for _ in range(config.GROQ_RPM_LIMIT):
        limiter.record(actual_tokens=10, now=1_000.0)

    with pytest.raises(RateLimitExceeded, match="RPM"):
        limiter.check(estimated_tokens=10, now=1_000.5)


def test_check_raises_when_tpm_exceeded():
    limiter = GroqRateLimiter()
    limiter.record(actual_tokens=config.GROQ_TPM_LIMIT - 50, now=1_000.0)

    with pytest.raises(RateLimitExceeded, match="TPM"):
        limiter.check(estimated_tokens=100, now=1_000.5)


def test_minute_window_expires_after_60_seconds():
    limiter = GroqRateLimiter()
    for _ in range(config.GROQ_RPM_LIMIT):
        limiter.record(actual_tokens=10, now=1_000.0)

    # 61 seconds later, the minute window should no longer count those calls.
    limiter.check(estimated_tokens=10, now=1_061.0)


def test_day_window_does_not_expire_within_60_seconds():
    limiter = GroqRateLimiter()
    for _ in range(config.GROQ_RPD_LIMIT):
        limiter.record(actual_tokens=1, now=1_000.0)

    with pytest.raises(RateLimitExceeded, match="RPD"):
        limiter.check(estimated_tokens=1, now=1_061.0)


def test_day_window_expires_after_24_hours():
    limiter = GroqRateLimiter()
    for _ in range(config.GROQ_RPD_LIMIT):
        limiter.record(actual_tokens=1, now=1_000.0)

    one_day_later = 1_000.0 + 86_400.0 + 1
    limiter.check(estimated_tokens=1, now=one_day_later)


def test_record_then_check_accumulates_tokens_within_the_minute():
    limiter = GroqRateLimiter()
    limiter.record(actual_tokens=4_000, now=1_000.0)
    limiter.record(actual_tokens=3_000, now=1_000.1)

    # 7,000 used + 1,500 estimated > 8,000 TPM limit.
    with pytest.raises(RateLimitExceeded, match="TPM"):
        limiter.check(estimated_tokens=1_500, now=1_000.2)

    # But 500 more fits under the 8,000 cap.
    limiter.check(estimated_tokens=500, now=1_000.2)


def test_estimate_tokens_scales_with_text_length():
    limiter = GroqRateLimiter()
    short = limiter.estimate_tokens("hello")
    long = limiter.estimate_tokens("hello " * 1000)
    assert long > short
