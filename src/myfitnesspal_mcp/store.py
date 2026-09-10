import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS day_nutrition (
    day TEXT PRIMARY KEY,
    calories REAL,
    protein REAL,
    carbs REAL,
    fat REAL,
    water_ml REAL,
    weight REAL,
    goal_calories REAL
);
CREATE TABLE IF NOT EXISTS diary_entry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    meal TEXT,
    name TEXT,
    calories REAL,
    protein REAL,
    carbs REAL,
    fat REAL
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
    PRIMARY KEY (day, component)
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
                self.conn.executescript(SCHEMA)
            if account_id is not None:
                self.bind_account(account_id, migrate_legacy=migrate_legacy)
        except Exception:
            self.conn.close()
            raise

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
                "SELECT fetched_at, complete FROM sync_state "
                "WHERE day = ? AND component = ?",
                (day, component),
            ).fetchone()
        if row is None:
            return None
        return {"fetched_at": row["fetched_at"], "complete": bool(row["complete"])}

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
                "INSERT INTO sync_state (day, component, fetched_at, complete) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(day, component) DO UPDATE SET "
                "fetched_at = excluded.fetched_at, complete = excluded.complete",
                (day, component, stamp, int(complete)),
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
        return _row_to_dict(row)

    def replace_diary(self, day: str, entries: list[dict], *, _commit: bool = True) -> None:
        with self._lock:
            try:
                self.conn.execute("DELETE FROM diary_entry WHERE day = ?", (day,))
                self.conn.executemany(
                    "INSERT INTO diary_entry "
                    "(day, meal, name, calories, protein, carbs, fat) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(
                        day, e.get("meal"), e.get("name"), e.get("calories"),
                        e.get("protein"), e.get("carbs"), e.get("fat"),
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
                "WHERE day = ? ORDER BY id",
                (day,),
            ).fetchall()
        return [dict(r) for r in rows]

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
