from myfitnesspal_mcp.rate_limit import RateLimitedSession, RequestRateLimiter


def test_rate_limiter_can_be_disabled():
    sleeps = []
    limiter = RequestRateLimiter(0, sleeper=sleeps.append)
    assert limiter.acquire() == 0
    assert sleeps == []


def test_rate_limiter_paces_requests_with_deterministic_clock():
    clock = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    limiter = RequestRateLimiter(
        60, burst=1, jitter_seconds=0, clock=lambda: clock[0], sleeper=sleep,
    )
    assert limiter.acquire() == 0
    assert limiter.acquire() == 1
    assert sleeps == [1]


def test_rate_limited_session_paces_each_request():
    calls = []

    class Session:
        def get(self, url):
            calls.append(url)
            return "ok"

    class Limiter:
        def __init__(self):
            self.calls = 0

        def acquire(self):
            self.calls += 1

    limiter = Limiter()
    session = RateLimitedSession(Session(), limiter)
    assert session.get("one") == "ok"
    assert session.get("two") == "ok"
    assert calls == ["one", "two"]
    assert limiter.calls == 2
