import asyncio

import pytest
from myfitnesspal.exceptions import MyfitnesspalLoginError

from myfitnesspal_mcp import mfp_client, server
from myfitnesspal_mcp.models import ServiceDayResult
from myfitnesspal_mcp.store import Store


class FakeContext:
    def __init__(self):
        self.messages = []

    async def info(self, message):
        self.messages.append(message)


def test_is_auth_error_patterns():
    assert mfp_client.is_auth_error(MyfitnesspalLoginError("bad"))
    assert mfp_client.is_auth_error(mfp_client.NotConnectedError("x"))
    assert mfp_client.is_auth_error(mfp_client.AuthenticationError("expired"))
    assert not mfp_client.is_auth_error(RuntimeError("HTTP 403 returned"))
    assert not mfp_client.is_auth_error(RuntimeError("couldn't read the csrf token"))
    assert not mfp_client.is_auth_error(RuntimeError("no food found for 'kale'"))
    assert not mfp_client.is_auth_error(ValueError("bad date"))


def test_run_with_refresh_retries_auth_failures(monkeypatch):
    refreshed = []
    monkeypatch.setattr(server.refresh, "refresh_session", lambda: refreshed.append(True))

    attempts = []

    def op():
        attempts.append(1)
        if len(attempts) == 1:
            raise MyfitnesspalLoginError("session expired")
        return "second try"

    ctx = FakeContext()
    result = asyncio.run(server.run_with_refresh(ctx, op))
    assert result == "second try"
    assert refreshed == [True]
    assert len(attempts) == 2
    assert "refreshing" in ctx.messages[0]
    assert "succeeded" in ctx.messages[1]


def test_run_with_refresh_gives_clear_error_when_retry_fails(monkeypatch):
    monkeypatch.setattr(server.refresh, "refresh_session", lambda: None)

    def op():
        raise MyfitnesspalLoginError("still expired")

    ctx = FakeContext()
    with pytest.raises(RuntimeError, match="myfitnesspal-mcp auth"):
        asyncio.run(server.run_with_refresh(ctx, op))
    assert "Session refresh failed." in ctx.messages


def test_run_with_refresh_does_not_retry_other_errors(monkeypatch):
    def explode():
        raise AssertionError("refresh must not run")

    monkeypatch.setattr(server.refresh, "refresh_session", explode)

    attempts = []

    def op():
        attempts.append(1)
        raise ValueError("no match for that food name")

    ctx = FakeContext()
    with pytest.raises(ValueError):
        asyncio.run(server.run_with_refresh(ctx, op))
    assert len(attempts) == 1
    assert ctx.messages == []


def test_run_with_refresh_supports_missing_context(monkeypatch):
    monkeypatch.setattr(server.refresh, "refresh_session", lambda: None)
    attempts = []

    def op():
        attempts.append(1)
        if len(attempts) == 1:
            raise MyfitnesspalLoginError("expired")
        return "ok"

    assert asyncio.run(server.run_with_refresh(None, op)) == "ok"


def test_log_food_does_not_replay_write_when_cache_refresh_auth_fails(
    local_store, monkeypatch
):
    class Client:
        effective_username = "test-user"

    client = Client()
    monkeypatch.setattr(server, "_store_account", server.config.account_key("test-user"))
    commits = []
    monkeypatch.setattr(server.mfp_client, "get_client", lambda: client)
    monkeypatch.setattr(
        server.diary, "prepare_food", lambda *args: {"prepared": True}
    )
    monkeypatch.setattr(
        server.diary, "commit_food", lambda client, item: commits.append(item) or {
            "matched": "Banana", "food_id": "111"
        }
    )
    class BrokenService:
        def __init__(self, store):
            assert store is local_store

        def get_day(self, day, force=False):
            raise MyfitnesspalLoginError("expired")

    monkeypatch.setattr(server, "NutritionService", BrokenService)

    result = asyncio.run(server.fitness_log_food("banana"))
    assert len(commits) == 1
    assert result["ok"] is True
    assert "cache refresh failed" in result["warnings"][0]


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: server.fitness_search_food(" "), "query must not be blank"),
        (lambda: server.fitness_search_food("x", limit=21), "limit must be between"),
        (lambda: server.fitness_log_food("x", meal="brunch"), "meal must be"),
        (lambda: server.fitness_log_food("x", quantity=float("nan")), "positive finite"),
        (lambda: server.fitness_log_food("x", food_id="1"), "supplied together"),
    ],
)
def test_tool_input_validation(call, message):
    with pytest.raises(ValueError, match=message):
        asyncio.run(call())


@pytest.mark.parametrize("rating", [0, 6])
def test_log_feel_rejects_invalid_rating(local_store, rating):
    with pytest.raises(ValueError, match="rating must be between"):
        server.fitness_log_feel(rating=rating)


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    test_store = Store(tmp_path / "server.db")
    monkeypatch.setattr(server, "_store", test_store)
    monkeypatch.setattr(server, "_store_account", None)
    monkeypatch.setattr(server, "_stores", {})
    monkeypatch.setattr(server.auth, "saved_username", lambda: None)
    return test_store


def test_get_store_switches_account_databases(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_store", None)
    monkeypatch.setattr(server, "_store_account", None)
    monkeypatch.setattr(server, "_stores", {})
    monkeypatch.setattr(server.config, "account_key", lambda username: username.casefold())
    monkeypatch.setattr(
        server.config, "database_path",
        lambda username=None: tmp_path / f"{username or 'legacy'}.db",
    )
    alice = server.get_store("Alice")
    alice.set_feel("2026-07-08", "alice", 4)
    bob = server.get_store("Bob")
    assert bob is not alice
    assert bob.feel("2026-07-08") is None
    assert alice.account_id() == "alice"
    assert bob.account_id() == "bob"
    assert server.get_store("ALICE") is alice


def test_with_session_uses_client_identity_without_saved_username(tmp_path, monkeypatch):
    class Client:
        effective_username = "token-user"

    monkeypatch.setattr(server, "_store", None)
    monkeypatch.setattr(server, "_store_account", None)
    monkeypatch.setattr(server, "_stores", {})
    monkeypatch.setattr(server.mfp_client, "get_client", lambda: Client())
    monkeypatch.setattr(server.config, "account_key", lambda username: username)
    monkeypatch.setattr(
        server.config, "database_path", lambda username=None: tmp_path / f"{username}.db"
    )
    result = asyncio.run(
        server.with_session(None, lambda store, client: (store.account_id(), client.effective_username))
    )
    assert result == ("token-user", "token-user")


def test_log_feel_tool(local_store):
    result = server.fitness_log_feel(note="strong", rating=5, date="2026-07-08")
    assert result == {"day": "2026-07-08", "note": "strong", "rating": 5}
    assert local_store.feel("2026-07-08")["rating"] == 5


def test_day_record_includes_note(local_store):
    local_store.set_note("2026-07-08", "long run, felt strong")
    assert local_store.day_record("2026-07-08")["note"] == "long run, felt strong"


def test_bulk_export_includes_note(local_store):
    local_store.set_note("2026-07-05", "rest day")
    result = asyncio.run(
        server.fitness_bulk_export(start="2026-07-01", end="2026-07-08")
    )
    assert result["count"] == 1
    assert result["days"][0]["note"] == "rest day"


def test_trends_rejects_unknown_metric(local_store):
    with pytest.raises(ValueError, match="unknown metric"):
        asyncio.run(server.fitness_get_trends(metric="steps"))


def test_bulk_export_validates_range(local_store):
    with pytest.raises(ValueError, match="start must be on or before end"):
        asyncio.run(server.fitness_bulk_export(start="2026-07-08", end="2026-07-01"))


def test_bulk_export_reads_cache_without_client(local_store):
    local_store.upsert_nutrition("2026-07-05", calories=1500.0)
    result = asyncio.run(
        server.fitness_bulk_export(start="2026-07-01", end="2026-07-08")
    )
    assert result["count"] == 1
    assert result["days"][0]["nutrition"]["calories"] == 1500.0


def test_get_day_delegates_to_nutrition_service(local_store, monkeypatch):
    calls = []

    class FakeService:
        def __init__(self, store):
            assert store is local_store

        def get_day(self, day):
            calls.append(day)
            return ServiceDayResult(
                data={
                    "day": day.isoformat(), "nutrition": {"weight": 175.0},
                    "diary": [], "note": None, "feel": None,
                },
                source="cache", retrieved_at=None,
            )

    monkeypatch.setattr(server, "NutritionService", FakeService)
    result = asyncio.run(server.fitness_get_day("2026-07-08"))
    assert calls == [server.parse_day("2026-07-08")]
    assert result["nutrition"]["weight"] == 175.0
