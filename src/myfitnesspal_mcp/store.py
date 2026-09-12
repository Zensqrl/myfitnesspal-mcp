import sqlite3
import threading
import hashlib
import json
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from . import config
from .models import AcquiredDay

SCHEMA = """
CREATE TABLE IF NOT EXISTS day_nutrition (
    day TEXT PRIMARY KEY,
    calories REAL,
    protein REAL,
    carbs REAL,
    fat REAL,
    water_ml REAL,
    weight REAL,
    goal_calories REAL,
    fiber REAL,
    sugar REAL,
    sodium_mg REAL,
    potassium_mg REAL,
    nutrients_json TEXT,
    goals_json TEXT,
    source_updated_at TEXT,
    retrieved_at TEXT,
    source_hash TEXT
);
CREATE TABLE IF NOT EXISTS diary_entry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    meal TEXT,
    name TEXT,
    calories REAL,
    protein REAL,
    carbs REAL,
    fat REAL,
    entry_key TEXT,
    position INTEGER,
    quantity REAL,
    serving_description TEXT,
    source_identifier TEXT,
    timestamp TEXT,
    nutrients_json TEXT,
    retrieved_at TEXT
);
CREATE INDEX IF NOT EXISTS diary_entry_day ON diary_entry(day);
CREATE TABLE IF NOT EXISTS feel_note (
    day TEXT PRIMARY KEY,
    note TEXT,
    rating INTEGER
);
CREATE TABLE IF NOT EXISTS day_note (
    day TEXT PRIMARY KEY,
    body TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS sync_state (
    day TEXT NOT NULL,
    component TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    complete INTEGER NOT NULL,
    last_attempt_at TEXT,
    last_success_at TEXT,
    status TEXT,
    error TEXT,
    source_hash TEXT,
    PRIMARY KEY (day, component)
);
CREATE TABLE IF NOT EXISTS raw_daily_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    parser_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS raw_daily_data_day_retrieved
ON raw_daily_data(day, retrieved_at);
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

NUTRITION_FIELDS = (
    "calories",
    "protein",
    "carbs",
    "fat",
    "water_ml",
    "weight",
    "goal_calories",
    "fiber",
    "sugar",
    "sodium_mg",
    "potassium_mg",
    "nutrients_json",
    "goals_json",
    "source_updated_at",
    "retrieved_at",
    "source_hash",
)

TREND_COLUMNS = {
    "weight": "weight",
    "calories_in": "calories",
    "protein": "protein",
    "carbs": "carbs",
    "fat": "fat",
}


POPULATED_DAY_QUERIES = (
    "SELECT day FROM day_nutrition WHERE day >= ? AND day <= ?",
    "SELECT DISTINCT day FROM diary_entry WHERE day >= ? AND day <= ?",
    "SELECT day FROM feel_note WHERE day >= ? AND day <= ?",
    "SELECT day FROM day_note WHERE day >= ? AND day <= ? "
    "AND body IS NOT NULL AND body != ''",
)


def trend_column(metric: str) -> str:
    column = TREND_COLUMNS.get(metric)
    if column is None:
        raise ValueError(f"unknown metric (use {' | '.join(TREND_COLUMNS)})")
    return column


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


class Store:
    def __init__(
        self,
        path: Path | None = None,
        account_id: str | None = None,
        migrate_legacy: bool = False,
    ):
        if path is None:
            path = config.database_path()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        try:
            self.conn.row_factory = sqlite3.Row
            self._lock = threading.RLock()
            with self._lock:
                self.conn.execute("PRAGMA foreign_keys = ON")
                self.conn.execute("PRAGMA busy_timeout = 5000")
                self.conn.execute("PRAGMA journal_mode = WAL")
                self.conn.executescript(SCHEMA)
                self._migrate_schema()
            if account_id is not None:
                self.bind_account(account_id, migrate_legacy=migrate_legacy)
        except Exception:
            self.conn.close()
            raise

    def _migrate_schema(self) -> None:
        """Upgrade unversioned pre-archive caches without deleting their data."""
        additions = {
            "day_nutrition": {
                "fiber": "REAL", "sugar": "REAL", "sodium_mg": "REAL",
                "potassium_mg": "REAL", "nutrients_json": "TEXT",
                "goals_json": "TEXT", "source_updated_at": "TEXT",
                "retrieved_at": "TEXT", "source_hash": "TEXT",
            },
            "diary_entry": {
                "entry_key": "TEXT", "position": "INTEGER", "quantity": "REAL",
                "serving_description": "TEXT", "source_identifier": "TEXT",
                "timestamp": "TEXT", "nutrients_json": "TEXT", "retrieved_at": "TEXT",
            },
            "sync_state": {
                "last_attempt_at": "TEXT", "last_success_at": "TEXT", "status": "TEXT",
                "error": "TEXT", "source_hash": "TEXT",
            },
        }
        for table, columns in additions.items():
            existing = {
                row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")
            }
            for name, declaration in columns.items():
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS diary_entry_key_unique "
            "ON diary_entry(day, entry_key) WHERE entry_key IS NOT NULL"
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (2, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    @contextmanager
    def transaction(self):
        """Serialize a transaction on the shared connection and roll it back on error."""
        with self._lock:
            try:
                yield
            except Exception:
                self.conn.rollback()
                raise
            else:
                self.conn.commit()

    def _has_user_data(self) -> bool:
        return any(
            self.conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            for table in ("day_nutrition", "diary_entry", "feel_note", "day_note")
        )

    def bind_account(self, account_id: str, migrate_legacy: bool = False) -> None:
        """Bind this database to one account.

        Existing unbound data is never silently claimed. Passing
        ``migrate_legacy=True`` is the explicit one-time migration operation.
        """
        account_id = account_id.strip()
        if not account_id:
            raise ValueError("account_id must not be empty")
        with self.transaction():
            row = self.conn.execute(
                "SELECT value FROM meta WHERE key = 'account_id'"
            ).fetchone()
            if row is not None and row["value"] != account_id:
                raise ValueError("cache belongs to a different account")
            if row is None and self._has_user_data() and not migrate_legacy:
                raise ValueError(
                    "legacy cache is unbound; pass migrate_legacy=True to claim it"
                )
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES ('account_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (account_id,),
            )

    def account_id(self) -> str | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM meta WHERE key = 'account_id'"
            ).fetchone()
        return None if row is None else row["value"]

    def component_status(self, day: str, component: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT fetched_at, complete, last_attempt_at, last_success_at, status, error, source_hash FROM sync_state "
                "WHERE day = ? AND component = ?",
                (day, component),
            ).fetchone()
        if row is None:
            return None
        return {
            "fetched_at": row["fetched_at"],
            "complete": bool(row["complete"]),
            "last_attempt_at": row["last_attempt_at"],
            "last_success_at": row["last_success_at"],
            "status": row["status"],
            "error": row["error"],
            "source_hash": row["source_hash"],
        }

    def mark_component(
        self,
        day: str,
        component: str,
        *,
        complete: bool,
        fetched_at: datetime | None = None,
        commit: bool = True,
    ) -> None:
        stamp = (fetched_at or datetime.now(timezone.utc)).isoformat()
        with self._lock:
            self.conn.execute(
                "INSERT INTO sync_state (day, component, fetched_at, complete, last_attempt_at, "
                "last_success_at, status, error) VALUES (?, ?, ?, ?, ?, ?, ?, NULL) "
                "ON CONFLICT(day, component) DO UPDATE SET fetched_at = excluded.fetched_at, "
                "complete = excluded.complete, last_attempt_at = excluded.last_attempt_at, "
                "last_success_at = excluded.last_success_at, status = excluded.status, error = NULL",
                (day, component, stamp, int(complete), stamp,
                 stamp if complete else None, "success" if complete else "incomplete"),
            )
            if commit:
                self.conn.commit()

    def upsert_nutrition(self, day: str, *, _commit: bool = True, **fields) -> None:
        unknown = set(fields) - set(NUTRITION_FIELDS)
        if unknown:
            raise ValueError(f"unknown nutrition fields: {sorted(unknown)}")
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{name} = excluded.{name}" for name in fields)
        if not fields:
            return
        with self._lock:
            self.conn.execute(
                f"INSERT INTO day_nutrition (day, {columns}) VALUES (?, {placeholders}) "
                f"ON CONFLICT(day) DO UPDATE SET {updates}",
                [day, *fields.values()],
            )
            if _commit:
                self.conn.commit()

    def nutrition(self, day: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM day_nutrition WHERE day = ?", (day,)
            ).fetchone()
        result = _row_to_dict(row)
        if result is None:
            return None
        for column, name in (("nutrients_json", "nutrients"), ("goals_json", "goals")):
            payload = result.pop(column, None)
            result[name] = json.loads(payload) if payload else {}
        return result

    def replace_diary(self, day: str, entries: list[dict], *, _commit: bool = True) -> None:
        with self._lock:
            try:
                self.conn.execute("DELETE FROM diary_entry WHERE day = ?", (day,))
                self.conn.executemany(
                    "INSERT INTO diary_entry "
                    "(day, meal, name, calories, protein, carbs, fat, entry_key, position, "
                    "quantity, serving_description, source_identifier, timestamp, nutrients_json, retrieved_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(
                        day, e.get("meal"), e.get("name"), e.get("calories"),
                        e.get("protein"), e.get("carbs"), e.get("fat"),
                        e.get("entry_key"), e.get("position"), e.get("quantity"),
                        e.get("serving_description"), e.get("source_identifier"),
                        e.get("timestamp"), e.get("nutrients_json"), e.get("retrieved_at"),
                    ) for e in entries],
                )
                if _commit:
                    self.conn.commit()
            except Exception:
                if _commit:
                    self.conn.rollback()
                raise

    def diary(self, day: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT meal, name, calories, protein, carbs, fat FROM diary_entry "
                "WHERE day = ? ORDER BY COALESCE(position, id), id",
                (day,),
            ).fetchall()
        return [dict(r) for r in rows]

    def food_entries(self, day: str) -> list[dict]:
        """Return the complete archived food-entry representation."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT meal, name, calories, protein, carbs, fat, quantity, serving_description, "
                "source_identifier, timestamp, nutrients_json, retrieved_at FROM diary_entry "
                "WHERE day = ? ORDER BY COALESCE(position, id), id",
                (day,),
            ).fetchall()
        entries = []
        for row in rows:
            entry = dict(row)
            payload = entry.pop("nutrients_json", None)
            entry["nutrients"] = json.loads(payload) if payload else {}
            entries.append(entry)
        return entries

    def set_feel(self, day: str, note: str | None, rating: int | None) -> dict:
        with self._lock:
            self.conn.execute(
                "INSERT INTO feel_note (day, note, rating) VALUES (?, ?, ?) "
                "ON CONFLICT(day) DO UPDATE SET note = excluded.note, rating = excluded.rating",
                (day, note, rating),
            )
            self.conn.commit()
        return {"day": day, "note": note, "rating": rating}

    def feel(self, day: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT day, note, rating FROM feel_note WHERE day = ?", (day,)
            ).fetchone()
        return _row_to_dict(row)

    def set_note(self, day: str, body: str | None, *, _commit: bool = True) -> None:
        """The MyFitnessPal daily diary note (synced from/to MFP), distinct from
        the local-only feel note."""
        with self._lock:
            self.conn.execute(
                "INSERT INTO day_note (day, body) VALUES (?, ?) "
                "ON CONFLICT(day) DO UPDATE SET body = excluded.body",
                (day, body),
            )
            if _commit:
                self.conn.commit()

    def note(self, day: str) -> str | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT body FROM day_note WHERE day = ?", (day,)
            ).fetchone()
        if row is None:
            return None
        return row["body"]

    def retrieved_at(self, day: str, component: str = "nutrition_diary") -> datetime | None:
        """Return the last known-good retrieval time for a component."""
        with self._lock:
            row = self.conn.execute(
                "SELECT COALESCE(last_success_at, fetched_at) AS stamp, complete "
                "FROM sync_state WHERE day = ? AND component = ?",
                (day, component),
            ).fetchone()
        if row is None or not row["complete"] or not row["stamp"]:
            return None
        try:
            value = datetime.fromisoformat(row["stamp"])
        except ValueError:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    def record_sync_attempt(self, day: str, component: str = "nutrition_diary") -> None:
        stamp = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self.conn.execute(
                "INSERT INTO sync_state (day, component, fetched_at, complete, last_attempt_at, status) "
                "VALUES (?, ?, ?, 0, ?, 'running') ON CONFLICT(day, component) DO UPDATE SET "
                "last_attempt_at = excluded.last_attempt_at, status = 'running', error = NULL",
                (day, component, stamp, stamp),
            )
            self.conn.commit()

    def record_sync_failure(
        self, day: str, error: str, component: str = "nutrition_diary"
    ) -> None:
        stamp = datetime.now(timezone.utc).isoformat()
        safe_error = error[:500]
        with self._lock:
            self.conn.execute(
                "INSERT INTO sync_state (day, component, fetched_at, complete, last_attempt_at, status, error) "
                "VALUES (?, ?, ?, 0, ?, 'failed', ?) ON CONFLICT(day, component) DO UPDATE SET "
                "last_attempt_at = excluded.last_attempt_at, status = 'failed', error = excluded.error",
                (day, component, stamp, stamp, safe_error),
            )
            self.conn.commit()

    @staticmethod
    def _entry_key(day: str, entry: dict, occurrence: int) -> str:
        identity = {
            "day": day,
            "meal": entry["meal"],
            "name": entry["name"],
            "quantity": entry.get("quantity"),
            "serving_description": entry.get("serving_description"),
            "source_identifier": entry.get("source_identifier"),
            "nutrients": entry.get("nutrients", {}),
            "occurrence": occurrence,
        }
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def persist_acquired_day(self, acquired: AcquiredDay) -> str:
        """Atomically archive one successful daily acquisition before it is returned."""
        day = acquired.day.isoformat()
        payload = json.dumps(acquired.raw_payload, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        stamp = acquired.retrieved_at.isoformat()
        totals = acquired.totals
        goals = acquired.goals
        nutrition = {
            "calories": totals.get("calories"),
            "protein": totals.get("protein"),
            "carbs": totals.get("carbohydrates", totals.get("carbs")),
            "fat": totals.get("fat"),
            "fiber": totals.get("fiber"),
            "sugar": totals.get("sugar"),
            "sodium_mg": totals.get("sodium"),
            "potassium_mg": totals.get("potass.", totals.get("potassium")),
            "water_ml": acquired.water_ml,
            "goal_calories": goals.get("calories"),
            "nutrients_json": json.dumps(totals, sort_keys=True, separators=(",", ":")),
            "goals_json": json.dumps(goals, sort_keys=True, separators=(",", ":")),
            "retrieved_at": stamp,
            "source_hash": payload_hash,
        }
        seen: dict[str, int] = {}
        entries = []
        for position, source in enumerate(acquired.entries):
            key_basis = json.dumps(
                {"meal": source.meal, "name": source.name, "nutrients": source.nutrients,
                 "quantity": source.quantity, "serving": source.serving_description},
                sort_keys=True, separators=(",", ":"),
            )
            occurrence = seen.get(key_basis, 0)
            seen[key_basis] = occurrence + 1
            entry = {
                "meal": source.meal,
                "name": source.name,
                "calories": source.nutrients.get("calories"),
                "protein": source.nutrients.get("protein"),
                "carbs": source.nutrients.get("carbohydrates", source.nutrients.get("carbs")),
                "fat": source.nutrients.get("fat"),
                "position": position,
                "quantity": source.quantity,
                "serving_description": source.serving_description,
                "source_identifier": source.source_identifier,
                "timestamp": source.timestamp.isoformat() if source.timestamp else None,
                "nutrients_json": json.dumps(source.nutrients, sort_keys=True, separators=(",", ":")),
                "retrieved_at": stamp,
            }
            entry["entry_key"] = source.source_identifier or self._entry_key(day, entry, occurrence)
            entries.append(entry)
        with self.transaction():
            self.upsert_nutrition(day, _commit=False, **nutrition)
            self.replace_diary(day, entries, _commit=False)
            if acquired.note_retrieved:
                self.set_note(day, acquired.note, _commit=False)
            self.conn.execute(
                "INSERT INTO raw_daily_data(day, retrieved_at, payload, payload_hash, parser_version) "
                "VALUES (?, ?, ?, ?, ?)",
                (day, stamp, payload, payload_hash, acquired.parser_version),
            )
            self.mark_component(
                day, "nutrition_diary", complete=True,
                fetched_at=acquired.retrieved_at, commit=False,
            )
            self.mark_component(
                day, "note", complete=acquired.note_retrieved,
                fetched_at=acquired.retrieved_at, commit=False,
            )
            self.conn.execute(
                "UPDATE sync_state SET source_hash = ? WHERE day = ? AND component = 'nutrition_diary'",
                (payload_hash, day),
            )
        return payload_hash

    def raw_daily_data(self, day: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT retrieved_at, payload, payload_hash, parser_version FROM raw_daily_data "
                "WHERE day = ? ORDER BY id", (day,),
            ).fetchall()
        return [
            {**dict(row), "payload": json.loads(row["payload"])}
            for row in rows
        ]

    def persist_weights(self, weights: dict[date, float], requested: list[date]) -> None:
        """Persist one range-level measurement read without touching diary data."""
        stamp = datetime.now(timezone.utc)
        with self.transaction():
            for day, value in weights.items():
                self.upsert_nutrition(day.isoformat(), weight=float(value), _commit=False)
            for day in requested:
                self.mark_component(day.isoformat(), "measurements", complete=True,
                                    fetched_at=stamp, commit=False)

    def days_with_nutrition(self, start: str, end: str) -> set[str]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT day FROM day_nutrition WHERE day >= ? AND day <= ?", (start, end)
            ).fetchall()
        return {r["day"] for r in rows}

    def trend(self, metric: str, start: str, end: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                f"SELECT day, {trend_column(metric)} AS value FROM day_nutrition "
                "WHERE day >= ? AND day <= ? AND value IS NOT NULL ORDER BY day",
                (start, end),
            ).fetchall()
        return [dict(r) for r in rows]

    def day_record(self, day: str) -> dict:
        with self._lock:
            return {
                "day": day,
                "nutrition": self.nutrition(day),
                "diary": self.diary(day),
                "note": self.note(day),
                "feel": self.feel(day),
            }

    def export_range(self, start: str, end: str) -> list[dict]:
        with self._lock:
            days: set[str] = set()
            for query in POPULATED_DAY_QUERIES:
                days |= {row["day"] for row in self.conn.execute(query, (start, end))}
            return [self.day_record(day) for day in sorted(days)]

    def last_synced_on(self) -> str | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM meta WHERE key = 'last_synced_on'"
            ).fetchone()
        if row is None:
            return None
        return row["value"]

    def mark_synced(self, day: date | None = None) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES ('last_synced_on', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ((day or date.today()).isoformat(),),
            )
            self.conn.commit()
