from datetime import timedelta

import pytest

from src.bot import RateLimiter, format_uptime, truncate_text


def test_rate_limiter_releases_old_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1000.0
    monkeypatch.setattr("src.bot.time.time", lambda: now)
    limiter = RateLimiter(2)

    assert limiter.is_allowed(1)
    assert limiter.is_allowed(1)
    assert not limiter.is_allowed(1)

    now = 1061.0
    assert limiter.is_allowed(1)


def test_format_uptime_and_truncation() -> None:
    assert format_uptime(timedelta(days=1, hours=2, minutes=3, seconds=4)) == "1d 2h 3m 4s"
    assert truncate_text("abcdef", 5) == "ab..."
