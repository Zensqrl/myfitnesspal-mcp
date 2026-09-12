"""Conservative request pacing for unofficial MyFitnessPal access."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable


logger = logging.getLogger(__name__)


class RequestRateLimiter:
    """A thread-safe token bucket with optional user-like request jitter."""

    def __init__(
        self,
        requests_per_minute: float,
        burst: int = 1,
        jitter_seconds: float = 0.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
    ):
        if requests_per_minute < 0:
            raise ValueError("requests_per_minute must not be negative")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        if jitter_seconds < 0:
            raise ValueError("jitter_seconds must not be negative")
        self.requests_per_minute = requests_per_minute
        self.burst = burst
        self.jitter_seconds = jitter_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._random_uniform = random_uniform
        self._lock = threading.Lock()
        self._tokens = float(burst)
        self._last_refill = clock()

    def acquire(self) -> float:
        """Reserve one request and return the time spent pacing it."""
        if self.requests_per_minute == 0:
            return 0.0
        rate_per_second = self.requests_per_minute / 60.0
        with self._lock:
            now = self._clock()
            if now >= self._last_refill:
                self._tokens = min(
                    float(self.burst),
                    self._tokens + (now - self._last_refill) * rate_per_second,
                )
                self._last_refill = now
            if self._tokens >= 1:
                self._tokens -= 1
                wait = 0.0
            else:
                base = max(now, self._last_refill)
                wait = (base - now) + (1.0 / rate_per_second)
                self._last_refill = base + (1.0 / rate_per_second)
            jitter = self._random_uniform(0.0, self.jitter_seconds)
            wait += jitter
        if wait:
            logger.info("mfp_request_rate_limited", extra={"wait_seconds": wait})
            self._sleeper(wait)
        return wait


class RateLimitedSession:
    """Proxy a requests-compatible session so every request passes the limiter."""

    def __init__(self, session, limiter: RequestRateLimiter):
        self._session = session
        self._limiter = limiter

    def request(self, *args, **kwargs):
        self._limiter.acquire()
        return self._session.request(*args, **kwargs)

    def get(self, *args, **kwargs):
        self._limiter.acquire()
        return self._session.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        self._limiter.acquire()
        return self._session.post(*args, **kwargs)

    def put(self, *args, **kwargs):
        self._limiter.acquire()
        return self._session.put(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._limiter.acquire()
        return self._session.delete(*args, **kwargs)

    def patch(self, *args, **kwargs):
        self._limiter.acquire()
        return self._session.patch(*args, **kwargs)

    def head(self, *args, **kwargs):
        self._limiter.acquire()
        return self._session.head(*args, **kwargs)

    def options(self, *args, **kwargs):
        self._limiter.acquire()
        return self._session.options(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._session, name)
