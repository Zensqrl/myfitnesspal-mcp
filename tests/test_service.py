from datetime import date, datetime, timedelta, timezone

import pytest

from myfitnesspal_mcp.freshness import FreshnessPolicy
from myfitnesspal_mcp.models import AcquiredDay, AcquiredFoodEntry
from myfitnesspal_mcp.service import NutritionService


TODAY = date(2026, 7, 8)
NOW = datetime(2026, 7, 8, 12, tzinfo=timezone.utc)


def acquired(day: date, *, retrieved_at: datetime = NOW) -> AcquiredDay:
    entry = AcquiredFoodEntry(
        meal="Breakfast", name="Oats", quantity=1.0, serving_description="cup",
        nutrients={"calories": 300.0, "protein": 10.0, "carbohydrates": 50.0, "fiber": 8.0},
    )
    return AcquiredDay(
        day=day, totals={"calories": 300.0, "protein": 10.0, "carbohydrates": 50.0, "fat": 5.0, "fiber": 8.0},
        goals={"calories": 2000.0, "protein": 100.0, "carbohydrates": 250.0, "fat": 70.0}, entries=[entry], water_ml=500.0,
        note="steady", note_retrieved=True, complete=False,
        raw_payload={"day": day.isoformat(), "totals": {"fiber": 8.0}, "meals": []},
        retrieved_at=retrieved_at,
    )


class FakeAcquirer:
    def __init__(self, snapshots=None, error=None):
        self.snapshots = snapshots or {}
        self.error = error
        self.days = []
        self.weights = {}
        self.weight_calls = []

    def fetch_day(self, day):
        self.days.append(day)
        if self.error:
            raise self.error
        return self.snapshots[day]

    def fetch_weights(self, start, end):
        self.weight_calls.append((start, end))
        return {day: value for day, value in self.weights.items() if start <= day <= end}


def policy(*, today_ttl=timedelta(0), mutable_days=30):
    return FreshnessPolicy(today_ttl, timedelta(hours=24), timedelta(hours=24), mutable_days)


def service(store, acquirer, freshness=None, publishers=()):
    return NutritionService(
        store, acquirer=acquirer, policy=freshness or policy(), publishers=publishers,
        today=lambda: TODAY, now=lambda: NOW, sleeper=lambda seconds: None,
        retry_attempts=1,
    )


def test_missing_day_fetches_and_persists_before_return(store):
    fake = FakeAcquirer({TODAY: acquired(TODAY)})
    result = service(store, fake).get_day(TODAY)
    assert result.source == "upstream"
    assert fake.days == [TODAY]
    assert result.data["nutrition"]["calories"] == 300.0
    assert store.raw_daily_data(TODAY.isoformat())[0]["payload"]["totals"]["fiber"] == 8.0


def test_fresh_immutable_cache_does_not_query_upstream(store):
    old = date(2024, 6, 14)
    store.persist_acquired_day(acquired(old))
    fake = FakeAcquirer(error=AssertionError("must not fetch"))
    result = service(store, fake, policy(mutable_days=30)).get_day(old)
    assert result.source == "cache"
    assert fake.days == []
    assert fake.weight_calls == []


def test_stale_cache_refreshes_and_failed_refresh_preserves_archive(store):
    yesterday = TODAY - timedelta(days=1)
    store.persist_acquired_day(acquired(yesterday, retrieved_at=NOW - timedelta(days=2)))
    fake = FakeAcquirer(error=ValueError("bad html"))
    result = service(store, fake).get_day(yesterday)
    assert result.source == "stale_cache"
    assert store.nutrition(yesterday.isoformat())["calories"] == 300.0
    assert store.component_status(yesterday.isoformat(), "nutrition_diary")["status"] == "failed"


def test_forced_refresh_bypasses_immutable_policy(store):
    old = date(2024, 6, 14)
    store.persist_acquired_day(acquired(old))
    fake = FakeAcquirer({old: acquired(old, retrieved_at=NOW)})
    result = service(store, fake).get_day(old, force=True)
    assert result.source == "upstream"
    assert fake.days == [old]


def test_repeated_sync_replaces_entries_and_archives_raw_history(store):
    fake = FakeAcquirer({TODAY: acquired(TODAY)})
    instance = service(store, fake)
    instance.sync_day(TODAY, force=True)
    instance.sync_day(TODAY, force=True)
    assert len(store.food_entries(TODAY.isoformat())) == 1
    assert len(store.raw_daily_data(TODAY.isoformat())) == 2


def test_backfill_resumes_by_skipping_successful_immutable_days(store):
    start = date(2024, 6, 14)
    end = start + timedelta(days=1)
    store.persist_acquired_day(acquired(start))
    fake = FakeAcquirer({end: acquired(end)})
    result = service(store, fake).backfill(start, end)
    assert result.skipped == 1
    assert result.refreshed == 1
    assert fake.days == [end]


def test_publisher_runs_only_after_successful_persistence(store):
    called = []

    class Publisher:
        def publish_current_state(self, result):
            assert store.nutrition(TODAY.isoformat()) is not None
            called.append(result.source)

    fake = FakeAcquirer({TODAY: acquired(TODAY)})
    service(store, fake, publishers=[Publisher()]).sync_day(TODAY)
    assert called == ["upstream"]


def test_missing_day_failure_is_raised(store):
    with pytest.raises(ValueError, match="bad html"):
        service(store, FakeAcquirer(error=ValueError("bad html"))).get_day(TODAY)


def test_incomplete_upstream_response_does_not_replace_valid_cache(store):
    from dataclasses import replace
    from myfitnesspal_mcp.service import IncompleteNutritionData
    store.persist_acquired_day(acquired(TODAY))
    incomplete = replace(acquired(TODAY), totals={}, goals={})
    fake = FakeAcquirer({TODAY: incomplete})
    result = service(store, fake).sync_day(TODAY, force=True)
    assert result.source == "stale_cache"
    assert store.nutrition(TODAY.isoformat())["calories"] == 300.0
    assert store.component_status(TODAY.isoformat(), "nutrition_diary")["error"] == IncompleteNutritionData.__name__
