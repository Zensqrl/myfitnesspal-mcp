"""MyFitnessPal-specific read acquisition, isolated from storage and MCP."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

from . import diary, mfp_client, refresh
from .models import AcquiredDay, AcquiredFoodEntry


logger = logging.getLogger(__name__)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numbers(values: dict | None) -> dict[str, float | None]:
    return {str(key): _number(value) for key, value in (values or {}).items()}


class MyFitnessPalAcquirer:
    """Adapt the existing client into secret-free, portable data models."""

    def __init__(
        self,
        client_factory: Callable[[], Any] = mfp_client.get_client,
        refresh_session: Callable[[], None] = refresh.refresh_session,
    ):
        self._client_factory = client_factory
        self._refresh_session = refresh_session

    def _read(self, operation: Callable[[Any], Any]) -> Any:
        try:
            return operation(self._client_factory())
        except Exception as exc:
            if not mfp_client.is_auth_error(exc):
                raise
            logger.info("mfp_auth_refresh_retry")
            self._refresh_session()
            return operation(self._client_factory())

    def fetch_day(self, day: date) -> AcquiredDay:
        return self._read(lambda client: self._fetch_day(client, day))

    def _fetch_day(self, client: Any, day: date) -> AcquiredDay:
        source_day = client.get_date(day)
        entries: list[AcquiredFoodEntry] = []
        raw_meals: list[dict[str, Any]] = []
        for meal in source_day.meals:
            raw_entries: list[dict[str, Any]] = []
            for entry in meal.entries:
                nutrients = _numbers(getattr(entry, "totals", {}))
                quantity = _number(getattr(entry, "quantity", None))
                serving = getattr(entry, "unit", None)
                model = AcquiredFoodEntry(
                    meal=str(meal.name).title(),
                    name=str(entry.name),
                    nutrients=nutrients,
                    quantity=quantity,
                    serving_description=None if serving is None else str(serving),
                )
                entries.append(model)
                raw_entries.append({
                    "name": model.name,
                    "quantity": model.quantity,
                    "serving_description": model.serving_description,
                    "nutrients": nutrients,
                })
            raw_meals.append({"name": str(meal.name), "entries": raw_entries})

        note: str | None = None
        note_retrieved = False
        note_error: str | None = None
        try:
            note = diary.get_note(client, day)
            note_retrieved = True
        except Exception as exc:
            if mfp_client.is_auth_error(exc):
                raise
            note_error = type(exc).__name__
            logger.warning("mfp_note_fetch_failed", extra={"day": day.isoformat()})

        totals = _numbers(getattr(source_day, "totals", {}))
        goals = _numbers(getattr(source_day, "goals", {}))
        water_ml = _number(getattr(source_day, "water", None))
        retrieved_at = datetime.now(timezone.utc)
        raw_payload: dict[str, Any] = {
            "day": day.isoformat(),
            "totals": totals,
            "goals": goals,
            "water_ml": water_ml,
            "complete": bool(getattr(source_day, "complete", False)),
            "meals": raw_meals,
            "note": note if note_retrieved else None,
            "note_retrieved": note_retrieved,
        }
        if note_error:
            raw_payload["note_error"] = note_error
        return AcquiredDay(
            day=day,
            totals=totals,
            goals=goals,
            entries=entries,
            water_ml=water_ml,
            note=note,
            note_retrieved=note_retrieved,
            complete=bool(getattr(source_day, "complete", False)),
            raw_payload=raw_payload,
            retrieved_at=retrieved_at,
        )

    def fetch_weights(self, start: date, end: date) -> dict[date, float]:
        def operation(client: Any) -> dict[date, float]:
            return {
                day: float(value)
                for day, value in client.get_measurements("Weight", start).items()
                if start <= day <= end
            }

        return self._read(operation)
