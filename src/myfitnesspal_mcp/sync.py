"""Compatibility helpers delegating synchronization to :mod:`service`.

New callers should construct ``NutritionService`` directly.  These functions
retain the prior internal API while ensuring there is only one freshness and
persistence implementation.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from . import config, mfp_client
from .acquisition import MyFitnessPalAcquirer
from .freshness import FreshnessPolicy
from .service import NutritionService, inclusive_days
from .store import Store


logger = logging.getLogger(__name__)
HISTORICAL_TTL = timedelta(hours=24)  # backwards-compatible export


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


def macros(totals: dict) -> dict:
    return {
        "calories": first_number(totals, "calories"),
        "protein": first_number(totals, "protein"),
        "carbs": first_number(totals, "carbohydrates", "carbs"),
        "fat": first_number(totals, "fat"),
    }


def days_to_fetch(cached: set[str], lookback: int, today: date) -> list[date]:
    past = (today - timedelta(days=offset) for offset in range(1, lookback))
    return [today] + [day for day in past if day.isoformat() not in cached]


def _service(store: Store, client, today: date) -> NutritionService:
    acquirer = MyFitnessPalAcquirer(
        client_factory=lambda: client,
        refresh_session=lambda: None,
    )
    return NutritionService(
        store,
        acquirer=acquirer,
        policy=FreshnessPolicy.from_config(),
        today=lambda: today,
        now=lambda: datetime.now(timezone.utc),
        retry_attempts=1,
    )


def refresh_day(store: Store, client, day: date) -> list[str]:
    return _service(store, client, day).sync_day(day, force=True).warnings


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
    """Compatibility façade for callers migrating to ``NutritionService``."""
    today = today or date.today()
    if (start is None) != (end is None):
        raise ValueError("start and end must be supplied together")
    if start is None:
        lookback = days or config.sync_days()
        if lookback < 1:
            raise ValueError("days must be at least 1")
        start, end = today - timedelta(days=lookback - 1), today
    assert start is not None and end is not None
    service = _service(store, client, today)
    warnings: list[str] = []
    requested = inclusive_days(start, end)
    for requested_day in requested:
        try:
            result = service.sync_day(requested_day, force=force)
            warnings.extend(result.warnings)
        except Exception as exc:
            if mfp_client.is_auth_error(exc):
                raise
            warning = f"sync for {requested_day} failed"
            logger.warning(warning)
            warnings.append(warning)
    try:
        due = force or any(
            not service.policy.is_immutable(day, today)
            and service.policy.requires_refresh(
                day, store.retrieved_at(day.isoformat(), "measurements"),
                today=today, now=datetime.now(timezone.utc),
            ) for day in requested
        )
        if due:
            store.persist_weights(service.acquirer.fetch_weights(start, end), requested)
    except Exception as exc:
        if mfp_client.is_auth_error(exc):
            raise
        warnings.append("weight measurements failed")
        logger.warning("weight measurements failed")
    store.mark_synced(today)
    return warnings
