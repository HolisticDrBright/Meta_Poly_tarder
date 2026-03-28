"""Tests for the token bucket rate limiter."""

import asyncio
import time
import pytest
from backend.data_layer.rate_limiter import RateLimiter


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_immediate_when_tokens_available(self):
        limiter = RateLimiter(name="test", max_tokens=5, refill_rate=10)
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.05  # should be instant

    @pytest.mark.asyncio
    async def test_waits_when_exhausted(self):
        limiter = RateLimiter(name="test", max_tokens=1, refill_rate=10)
        await limiter.acquire()  # use the only token
        start = time.monotonic()
        await limiter.acquire()  # should wait ~0.1s for refill
        elapsed = time.monotonic() - start
        assert elapsed >= 0.05  # waited for at least partial refill

    @pytest.mark.asyncio
    async def test_burst_capacity(self):
        limiter = RateLimiter(name="test", max_tokens=5, refill_rate=1)
        # Should be able to burst 5 immediately
        for _ in range(5):
            await limiter.acquire()
        # 6th should wait
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.5

    def test_available_tokens(self):
        limiter = RateLimiter(name="test", max_tokens=3, refill_rate=1)
        assert limiter.available_tokens == pytest.approx(3.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_tokens_never_exceed_max(self):
        limiter = RateLimiter(name="test", max_tokens=2, refill_rate=100)
        await asyncio.sleep(0.1)  # let it refill way past max
        assert limiter.available_tokens <= 2.0
