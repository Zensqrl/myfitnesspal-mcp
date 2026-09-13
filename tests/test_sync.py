import datetime

import pytest
from myfitnesspal.exceptions import MyfitnesspalLoginError

from myfitnesspal_mcp import sync

TODAY = datetime.date(2026, 7, 8)


def test_days_to_fetch_gaps_only():
    existing = {"2026-07-07", "2026-07-05"}
    days = sync.days_to_fetch(existing, lookback=4, today=TODAY)
    assert days == [TODAY, datetime.date(2026, 7, 6)]


def test_days_to_fetch_always_includes_today():
    days = sync.days_to_fetch({TODAY.isoformat()}, lookback=1, today=TODAY)
    assert days == [TODAY]


def test_first_number_prefers_first_present_key():
    assert sync.first_number({"carbohydrates": 10}, "carbohydrates", "carbs") == 10.0
    assert sync.first_number({"carbs": 5}, "carbohydrates", "carbs") == 5.0
    assert sync.first_number({}, "carbohydrates", "carbs") is None


class FakeEntry:
    def __init__(self, name, totals):
        self.name = name
        self.totals = totals


class FakeMeal:
    def __init__(self, name, entries):
        self.name = name
        self.entries = entries


class FakeDay:
    def __init__(self):
        self.totals = {"calories": 2100, "protein": 150, "carbohydrates": 200, "fat": 70}
        self.goals = {"calories": 2200}
        self.water = 750
        self.meals = [
            FakeMeal("breakfast", [FakeEntry("Oats", {"calories": 300, "protein": 10})]),
            FakeMeal("lunch", []),
        ]


class FakeNoteResponse:
    def __init__(self, body):
        self._body = body

    def json(self):
        return {"item": {"body": self._body}}

    def raise_for_status(self):
        pass


class FakeNoteSession:
    def __init__(self, body):
        self.body = body

    def get(self, url, **kwargs):
        return FakeNoteResponse(self.body)


class FakeSyncClient:
    BASE_URL_SECURE = "https://www.myfitnesspal.com/"

    def __init__(self, note_body="today felt great"):
        self.fetched = []
        self.weights = {}
        self.access_token = "fake-token"
        self.user_id = "user-1"
        self.effective_username = "tester"
        self.session = FakeNoteSession(note_body)

    def get_date(self, day):
        self.fetched.append(day)
        return FakeDay()

    def get_measurements(self, kind, earliest):
        self.measurements_earliest = earliest
        return {day: value for day, value in self.weights.items() if day >= earliest}


def test_refresh_day_populates_store(store):
    client = FakeSyncClient()
    sync.refresh_day(store, client, TODAY)
    nutrition = store.nutrition(TODAY.isoformat())
    assert nutrition["calories"] == 2100.0
    assert nutrition["carbs"] == 200.0
    assert nutrition["water_ml"] == 750.0
    assert nutrition["goal_calories"] == 2200.0
    entries = store.diary(TODAY.isoformat())
    assert entries == [
        {
            "meal": "Breakfast",
            "name": "Oats",
            "calories": 300.0,
            "protein": 10.0,
            "carbs": None,
            "fat": None,
        }
    ]
    assert store.note(TODAY.isoformat()) == "today felt great"


def test_poll_fetches_today_once_each_call(store, monkeypatch):
    client = FakeSyncClient()
    store.mark_synced(TODAY)
    sync.poll(store, client, days=1, today=TODAY)
    assert client.fetched == [TODAY]
    sync.poll(store, client, force=True, days=1, today=TODAY)
    assert client.fetched == [TODAY, TODAY]


def test_poll_records_weights(store):
    client = FakeSyncClient()
    client.weights = {TODAY: 80.0, TODAY - datetime.timedelta(days=400): 90.0}
    sync.poll(store, client, days=3, force=True, today=TODAY)
    assert store.nutrition(TODAY.isoformat())["weight"] == 80.0


def test_poll_backfills_weight_for_already_cached_day(store):
    """A weigh-in lands on a day whose nutrition is already cached.

    Such a day is absent from `days_to_fetch`, so keying the measurement query
    off the fetched days would strand the weight permanently.
    """
    window_start = TODAY - datetime.timedelta(days=2)
    yesterday = TODAY - datetime.timedelta(days=1)
    for day in (window_start, yesterday):
        store.upsert_nutrition(day.isoformat(), calories=2000.0)
        store.mark_component(day.isoformat(), "nutrition_diary", complete=True)
        store.mark_component(day.isoformat(), "note", complete=True)

    client = FakeSyncClient()
    client.weights = {yesterday: 79.5}
    sync.poll(store, client, days=3, today=TODAY)

    assert client.fetched == [TODAY]
    assert client.measurements_earliest == window_start
    assert store.nutrition(yesterday.isoformat())["weight"] == 79.5


def test_poll_propagates_auth_errors(store):
    class ExpiredClient(FakeSyncClient):
        def get_date(self, day):
            raise MyfitnesspalLoginError("session expired")

    with pytest.raises(MyfitnesspalLoginError):
        sync.poll(store, ExpiredClient(), days=2, force=True)


def test_poll_skips_bad_days_without_auth_errors(store):
    class FlakyClient(FakeSyncClient):
        def get_date(self, day):
            self.fetched.append(day)
            if len(self.fetched) == 1:
                raise ValueError("parse error")
            return FakeDay()

    client = FlakyClient()
    sync.poll(store, client, days=2, force=True)
    assert len(client.fetched) == 2


def test_poll_range_is_inclusive_and_weight_only_day_is_refetched(store):
    start = TODAY - datetime.timedelta(days=2)
    store.upsert_nutrition(start.isoformat(), weight=79.0)
    client = FakeSyncClient()
    sync.poll(store, client, start=start, end=TODAY, today=TODAY)
    assert client.fetched == [start, start + datetime.timedelta(days=1), TODAY]


def test_incomplete_day_retries_but_fresh_complete_historical_day_skips(store):
    yesterday = TODAY - datetime.timedelta(days=1)
    store.mark_component(yesterday.isoformat(), "nutrition_diary", complete=False)
    client = FakeSyncClient()
    sync.poll(store, client, start=yesterday, end=yesterday, today=TODAY)
    assert client.fetched == [yesterday]
    client.fetched.clear()
    sync.poll(store, client, start=yesterday, end=yesterday, today=TODAY)
    assert client.fetched == []


def test_refresh_failure_preserves_cached_day_and_returns_warning(store):
    yesterday = TODAY - datetime.timedelta(days=1)
    store.upsert_nutrition(yesterday.isoformat(), calories=123)
    store.replace_diary(yesterday.isoformat(), [{"name": "Old"}])

    class BrokenClient(FakeSyncClient):
        def get_date(self, day):
            raise ValueError("bad parse")

    warnings = sync.poll(
        store, BrokenClient(), start=yesterday, end=yesterday, today=TODAY
    )
    assert warnings and "sync for" in warnings[0]
    assert store.nutrition(yesterday.isoformat())["calories"] == 123
    assert store.diary(yesterday.isoformat())[0]["name"] == "Old"
    assert store.component_status(yesterday.isoformat(), "nutrition_diary")["complete"] is False


def test_note_failure_is_incomplete_and_retried(store):
    yesterday = TODAY - datetime.timedelta(days=1)

    class BrokenNoteSession(FakeNoteSession):
        def get(self, url, **kwargs):
            raise ValueError("note unavailable")

    client = FakeSyncClient()
    client.session = BrokenNoteSession(None)
    warnings = sync.poll(store, client, start=yesterday, end=yesterday, today=TODAY)
    assert any("note fetch" in warning for warning in warnings)
    assert store.component_status(yesterday.isoformat(), "nutrition_diary")["complete"] is True
    assert store.component_status(yesterday.isoformat(), "note")["complete"] is False

    client.session = FakeNoteSession("recovered")
    client.fetched.clear()
    sync.poll(store, client, start=yesterday, end=yesterday, today=TODAY)
    assert client.fetched == [yesterday]
    assert store.note(yesterday.isoformat()) == "recovered"
