import logging
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from . import config, diary
from .mfp_client import is_auth_error
from .store import Store

logger = logging.getLogger(__name__)


@contextmanager
def tolerating_failures(description: str):
    """Auth failures propagate so the caller can refresh the session and retry;
    everything else is logged and skipped."""
    try:
        yield
    except Exception as exc:
        if is_auth_error(exc):
            raise
        logger.warning("%s failed (%s)", description, type(exc).__name__)


def as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_number(values: dict, *keys) -> float | None:
    for key in keys:
        number = as_float(values.get(key))
        if number is not None:
            return number
    return None


HISTORICAL_TTL = timedelta(hours=24)


def days_to_fetch(cached: set[str], lookback: int, today: date) -> list[date]:
    """Today is always refetched because the diary is live; past days only when
    the cache has no row for them."""
    past = (today - timedelta(days=offset) for offset in range(1, lookback))
    return [today] + [day for day in past if day.isoformat() not in cached]


def inclusive_days(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end must be on or after start")
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _is_fresh_complete(
    store: Store, day: date, component: str, now: datetime
) -> bool:
    status = store.component_status(day.isoformat(), component)
    if status is None or not status["complete"]:
        return False
    try:
        fetched = datetime.fromisoformat(status["fetched_at"])
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    return now - fetched <= HISTORICAL_TTL


def macros(totals: dict) -> dict:
    return {
        "calories": first_number(totals, "calories"),
        "protein": first_number(totals, "protein"),
        "carbs": first_number(totals, "carbohydrates", "carbs"),
        "fat": first_number(totals, "fat"),
    }


def refresh_day(store: Store, client, day: date) -> list[str]:
    mfp_day = client.get_date(day)
    key = day.isoformat()
    nutrition = {
        **macros(mfp_day.totals),
        "water_ml": as_float(mfp_day.water),
        "goal_calories": first_number(mfp_day.goals or {}, "calories"),
    }
    entries = [
        {"meal": str(meal.name).title(), "name": entry.name, **macros(entry.totals)}
        for meal in mfp_day.meals
        for entry in meal.entries
    ]
    warnings = []
    note_ok = False
    try:
        note = diary.get_note(client, day)
        note_ok = True
    except Exception as exc:
        if is_auth_error(exc):
            raise
        warnings.append(f"note fetch for {day} failed")
        logger.warning(warnings[-1])

    # A failed fetch cannot partially replace a previously good cached day.
    with store.transaction():
        store.upsert_nutrition(key, _commit=False, **nutrition)
        store.replace_diary(key, entries, _commit=False)
        store.mark_component(key, "nutrition_diary", complete=True, commit=False)
        if note_ok:
            store.set_note(key, note, _commit=False)
        store.mark_component(key, "note", complete=note_ok, commit=False)
    return warnings


def poll(
    store: Store,
    client,
    days: int | None = None,
    force: bool = False,
    today: date | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[str]:
    today = today or date.today()
    if (start is None) != (end is None):
        raise ValueError("start and end must be supplied together")
    if start is None:
        lookback = days or config.sync_days()
        if lookback < 1:
            raise ValueError("days must be at least 1")
        start, end = today - timedelta(days=lookback - 1), today
    requested = inclusive_days(start, end)
    now = datetime.now(timezone.utc)
    fetch_days = [
        day for day in requested
        if force
        or day == today
        or not _is_fresh_complete(store, day, "nutrition_diary", now)
        or not _is_fresh_complete(store, day, "note", now)
    ]
    warnings: list[str] = []
    for day in fetch_days:
        try:
            warnings.extend(refresh_day(store, client, day))
        except Exception as exc:
            if is_auth_error(exc):
                raise
            warning = f"sync for {day} failed"
            warnings.append(warning)
            logger.warning(warning)
            # Preserve cached content but record that this component must retry.
            store.mark_component(day.isoformat(), "nutrition_diary", complete=False)

    measurements_due = force or today in requested or any(
        not _is_fresh_complete(store, day, "measurements", now)
        for day in requested
    )
    try:
        if not measurements_due:
            store.mark_synced(today)
            return warnings
        weights = client.get_measurements("Weight", start)
        with store.transaction():
            for day, value in weights.items():
                if start <= day <= end:
                    store.upsert_nutrition(day.isoformat(), weight=float(value), _commit=False)
            for requested_day in requested:
                store.mark_component(
                    requested_day.isoformat(), "measurements", complete=True, commit=False
                )
    except Exception as exc:
        if is_auth_error(exc):
            raise
        warning = "weight measurements failed"
        warnings.append(warning)
        logger.warning(warning)
        for requested_day in requested:
            store.mark_component(requested_day.isoformat(), "measurements", complete=False)
    store.mark_synced(today)
    return warnings
