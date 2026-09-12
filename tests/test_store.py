import pytest
from datetime import date, datetime, timezone
import sqlite3

from myfitnesspal_mcp.models import AcquiredDay, AcquiredFoodEntry
from myfitnesspal_mcp.store import Store


def test_upsert_nutrition_partial_updates(store):
    store.upsert_nutrition("2026-07-01", calories=1800.0, protein=120.0)
    store.upsert_nutrition("2026-07-01", weight=80.5)
    row = store.nutrition("2026-07-01")
    assert row["calories"] == 1800.0
    assert row["protein"] == 120.0
    assert row["weight"] == 80.5


def test_upsert_nutrition_rejects_unknown_fields(store):
    with pytest.raises(ValueError, match="unknown nutrition fields"):
        store.upsert_nutrition("2026-07-01", steps=10000)


def test_replace_diary(store):
    store.replace_diary("2026-07-01", [{"meal": "Breakfast", "name": "Egg", "calories": 70}])
    store.replace_diary(
        "2026-07-01",
        [
            {"meal": "Breakfast", "name": "Oats", "calories": 300},
            {"meal": "Lunch", "name": "Salad", "calories": 250},
        ],
    )
    entries = store.diary("2026-07-01")
    assert [e["name"] for e in entries] == ["Oats", "Salad"]


def test_replace_diary_rolls_back_standalone_failure(store):
    store.replace_diary("2026-07-01", [{"meal": "Breakfast", "name": "Egg"}])

    class BrokenEntry:
        def get(self, key):
            raise ValueError("bad entry")

    with pytest.raises(ValueError, match="bad entry"):
        store.replace_diary("2026-07-01", [BrokenEntry()])
    assert [e["name"] for e in store.diary("2026-07-01")] == ["Egg"]


def test_feel_upsert(store):
    store.set_feel("2026-07-01", "tired", 2)
    store.set_feel("2026-07-01", "better after coffee", 4)
    assert store.feel("2026-07-01") == {
        "day": "2026-07-01",
        "note": "better after coffee",
        "rating": 4,
    }
    assert store.feel("2026-07-02") is None


def test_note_upsert(store):
    store.set_note("2026-07-01", "first draft")
    store.set_note("2026-07-01", "final note")
    assert store.note("2026-07-01") == "final note"
    assert store.note("2026-07-02") is None


def test_trend_filters_nulls_and_orders(store):
    store.upsert_nutrition("2026-07-02", weight=81.0)
    store.upsert_nutrition("2026-07-01", weight=80.0)
    store.upsert_nutrition("2026-07-03", calories=2000.0)
    points = store.trend("weight", "2026-07-01", "2026-07-31")
    assert points == [
        {"day": "2026-07-01", "value": 80.0},
        {"day": "2026-07-02", "value": 81.0},
    ]


def test_trend_unknown_metric(store):
    with pytest.raises(ValueError, match="unknown metric"):
        store.trend("steps", "2026-07-01", "2026-07-31")


def test_days_with_nutrition(store):
    store.upsert_nutrition("2026-07-01", calories=1.0)
    store.upsert_nutrition("2026-07-05", calories=1.0)
    assert store.days_with_nutrition("2026-07-01", "2026-07-04") == {"2026-07-01"}


def test_export_range_unions_sources(store):
    store.upsert_nutrition("2026-07-01", calories=2000.0)
    store.replace_diary("2026-07-02", [{"meal": "Lunch", "name": "Soup"}])
    store.set_feel("2026-07-03", "good", 5)
    store.set_note("2026-07-04", "walked 10k steps")
    days = store.export_range("2026-07-01", "2026-07-31")
    assert [d["day"] for d in days] == [
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
    ]
    assert days[1]["diary"][0]["name"] == "Soup"
    assert days[2]["feel"]["rating"] == 5
    assert days[3]["note"] == "walked 10k steps"


def test_export_range_ignores_empty_notes(store):
    store.set_note("2026-07-05", None)
    store.set_note("2026-07-06", "")
    assert store.export_range("2026-07-01", "2026-07-31") == []


def test_mark_synced_roundtrip(store):
    assert store.last_synced_on() is None
    store.mark_synced()
    assert store.last_synced_on() is not None


def test_component_status_is_independent_of_nutrition_rows(store):
    store.upsert_nutrition("2026-07-01", weight=80)
    assert store.component_status("2026-07-01", "nutrition_diary") is None
    store.mark_component("2026-07-01", "measurements", complete=True)
    assert store.component_status("2026-07-01", "measurements")["complete"] is True


def test_account_binding_requires_explicit_legacy_migration(tmp_path):
    path = tmp_path / "legacy.db"
    legacy = Store(path)
    legacy.upsert_nutrition("2026-07-01", calories=1)
    legacy.conn.close()
    with pytest.raises(ValueError, match="legacy cache is unbound"):
        Store(path, account_id="account-a")
    migrated = Store(path, account_id="account-a", migrate_legacy=True)
    assert migrated.account_id() == "account-a"
    with pytest.raises(ValueError, match="different account"):
        migrated.bind_account("account-b")


def _acquired(day="2026-07-01", calories=100):
    return AcquiredDay(
        day=date.fromisoformat(day), totals={"calories": calories}, goals={},
        entries=[AcquiredFoodEntry("Breakfast", "Egg", {"calories": calories})],
        water_ml=None, note=None, note_retrieved=True, complete=False,
        raw_payload={"day": day, "calories": calories},
        retrieved_at=datetime.now(timezone.utc),
    )


def test_archive_transaction_rolls_back_partial_day_replacement(store, monkeypatch):
    store.persist_acquired_day(_acquired(calories=100))

    def fail(*args, **kwargs):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(store, "replace_diary", fail)
    with pytest.raises(RuntimeError, match="insert failed"):
        store.persist_acquired_day(_acquired(calories=200))
    assert store.nutrition("2026-07-01")["calories"] == 100
    assert len(store.raw_daily_data("2026-07-01")) == 1


def test_existing_database_is_migrated_in_place(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE day_nutrition (day TEXT PRIMARY KEY, calories REAL, protein REAL,
                carbs REAL, fat REAL, water_ml REAL, weight REAL, goal_calories REAL);
            CREATE TABLE diary_entry (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL,
                meal TEXT, name TEXT, calories REAL, protein REAL, carbs REAL, fat REAL);
            CREATE TABLE feel_note (day TEXT PRIMARY KEY, note TEXT, rating INTEGER);
            CREATE TABLE day_note (day TEXT PRIMARY KEY, body TEXT);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE sync_state (day TEXT NOT NULL, component TEXT NOT NULL,
                fetched_at TEXT NOT NULL, complete INTEGER NOT NULL, PRIMARY KEY(day, component));
            INSERT INTO day_nutrition(day, calories) VALUES ('2024-06-14', 1800);
        """)
    migrated = Store(path)
    assert migrated.nutrition("2024-06-14")["calories"] == 1800
    assert migrated.conn.execute("SELECT 1 FROM raw_daily_data").fetchone() is None
    columns = {row["name"] for row in migrated.conn.execute("PRAGMA table_info(day_nutrition)")}
    assert {"nutrients_json", "retrieved_at", "source_hash"} <= columns
