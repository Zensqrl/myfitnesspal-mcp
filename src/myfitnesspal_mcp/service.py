"""Reusable nutrition orchestration over local storage and MyFitnessPal."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from . import config, mfp_client
from .acquisition import MyFitnessPalAcquirer
from .freshness import FreshnessPolicy
from .models import BackfillResult, ServiceDayResult, SyncResult
from .publishers import NutritionPublisher
from .store import Store


logger = logging.getLogger(__name__)


class IncompleteNutritionData(RuntimeError):
    """The upstream diary response did not contain the required daily contract."""


def validate_nutrition_contract(acquired) -> None:
    required = ("calories", "protein", "carbohydrates", "fat")
    for values in (acquired.totals, acquired.goals):
        for key in required:
            value = values.get(key, values.get("carbs") if key == "carbohydrates" else None)
            if not isinstance(value, (int, float)):
                raise IncompleteNutritionData(
                    "MyFitnessPal returned an incomplete daily nutrition response."
                )


def inclusive_days(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end must be on or after start")
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


class NutritionService:
    """The single owner of freshness, acquisition, and archive persistence."""

    def __init__(
        self,
        store: Store,
        acquirer: MyFitnessPalAcquirer | None = None,
        policy: FreshnessPolicy | None = None,
        publishers: Iterable[NutritionPublisher] = (),
        *,
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleeper: Callable[[float], None] = time.sleep,
        progress: Callable[[str], None] | None = None,
        retry_attempts: int | None = None,
        retry_backoff_seconds: float | None = None,
    ):
        self.store = store
        self.acquirer = acquirer or MyFitnessPalAcquirer()
        self.policy = policy or FreshnessPolicy.from_config()
        self.publishers = tuple(publishers)
        self._today = today
        self._now = now
        self._sleeper = sleeper
        self._progress = progress or (lambda _message: None)
        self.propagate_auth_errors = False
        self._retry_attempts = retry_attempts or config.retry_attempts()
        self._retry_backoff_seconds = (
            config.retry_backoff_seconds()
            if retry_backoff_seconds is None else retry_backoff_seconds
        )

    def _cached_result(self, day: date, source: str, warnings: list[str] | None = None) -> ServiceDayResult:
        return ServiceDayResult(
            data=self.store.day_record(day.isoformat()),
            source=source,
            retrieved_at=self.store.retrieved_at(day.isoformat()),
            warnings=warnings or [],
        )

    def _has_cached_day(self, day: date) -> bool:
        record = self.store.day_record(day.isoformat())
        return any(record[key] is not None and record[key] != [] for key in ("nutrition", "diary", "note"))

    def _publish(self, result: ServiceDayResult) -> list[str]:
        warnings: list[str] = []
        if result.data["day"] != self._today().isoformat():
            return warnings
        for publisher in self.publishers:
            try:
                publisher.publish_current_state(result)
            except Exception as exc:
                logger.warning("nutrition_publish_failed", extra={"error": type(exc).__name__})
                warnings.append("current-state publisher failed")
        return warnings

    def sync_day(self, day: date, *, force: bool = False) -> SyncResult:
        self._progress(f"Checking archive status for {day.isoformat()}...")
        stamp = self.store.retrieved_at(day.isoformat())
        note_status = self.store.component_status(day.isoformat(), "note")
        nutrition_due = self.policy.requires_refresh(
            day, stamp, today=self._today(), now=self._now(), force=force
        )
        note_due = note_status is not None and not note_status["complete"]
        if not nutrition_due and not note_due:
            logger.info("nutrition_cache_hit", extra={"day": day.isoformat()})
            self._progress(f"Using cached data for {day.isoformat()}.")
            return SyncResult(day=day, source="cache", refreshed=False)
        logger.info("nutrition_upstream_fetch", extra={"day": day.isoformat(), "force": force})
        self._progress(f"Fetching diary and nutrition for {day.isoformat()} from MyFitnessPal...")
        self.store.record_sync_attempt(day.isoformat())
        last_error: Exception | None = None
        for attempt in range(self._retry_attempts):
            try:
                acquired = self.acquirer.fetch_day(day)
                validate_nutrition_contract(acquired)
                self.store.persist_acquired_day(acquired)
                self.store.mark_synced(day)
                result = self._cached_result(day, "upstream")
                warnings = self._publish(result)
                if not acquired.note_retrieved:
                    warnings.append(f"note fetch for {day.isoformat()} failed")
                logger.info("nutrition_sync_completed", extra={"day": day.isoformat()})
                self._progress(f"Archived diary and nutrition for {day.isoformat()}.")
                return SyncResult(day=day, source="upstream", refreshed=True, warnings=warnings)
            except Exception as exc:
                last_error = exc
                if mfp_client.is_auth_error(exc) or attempt + 1 >= self._retry_attempts:
                    break
                delay = self._retry_backoff_seconds * (2 ** attempt)
                logger.warning("nutrition_sync_retry", extra={
                    "day": day.isoformat(), "attempt": attempt + 1, "delay_seconds": delay,
                    "error": type(exc).__name__,
                })
                self._progress(
                    f"Fetch for {day.isoformat()} failed; retrying in {delay:g} seconds..."
                )
                self._sleeper(delay)
        assert last_error is not None
        self.store.record_sync_failure(day.isoformat(), type(last_error).__name__)
        if self.propagate_auth_errors and mfp_client.is_auth_error(last_error):
            raise last_error
        logger.warning("nutrition_sync_failed", extra={"day": day.isoformat(), "error": type(last_error).__name__})
        if self._has_cached_day(day):
            return SyncResult(
                day=day, source="stale_cache", refreshed=False,
                warnings=[f"sync for {day.isoformat()} failed; serving archived data"],
            )
        raise last_error

    def get_day(self, day: date, *, force: bool = False) -> ServiceDayResult:
        sync = self.sync_range(day, day, force=force)[0]
        result = self._cached_result(day, sync.source, list(sync.warnings))
        return result

    def sync_range(self, start: date, end: date, *, force: bool = False) -> list[SyncResult]:
        days = inclusive_days(start, end)
        results: list[SyncResult] = []
        for day in days:
            results.append(self.sync_day(day, force=force))
        measurements_due = force or any(
            not self.policy.is_immutable(day, self._today())
            and self.policy.requires_refresh(
                day, self.store.retrieved_at(day.isoformat(), "measurements"),
                today=self._today(), now=self._now(), force=False,
            ) for day in days
        )
        if measurements_due:
            self._progress(
                f"Fetching weight measurements for {start.isoformat()} through {end.isoformat()}..."
            )
            try:
                self.store.persist_weights(self.acquirer.fetch_weights(start, end), days)
                self._progress("Archived weight measurements.")
            except Exception as exc:
                if mfp_client.is_auth_error(exc):
                    raise
                warning = "weight measurements failed"
                logger.warning("nutrition_measurements_failed", extra={"error": type(exc).__name__})
                results = [replace(result, warnings=[*result.warnings, warning]) for result in results]
        return results

    def sync_recent(self, days: int | None = None, *, force: bool = False) -> list[SyncResult]:
        count = days or config.sync_days()
        if count < 1:
            raise ValueError("days must be at least 1")
        today = self._today()
        return self.sync_range(today - timedelta(days=count - 1), today, force=force)

    def get_trend(self, metric: str, start: date, end: date, *, force: bool = False) -> tuple[list[dict], list[str]]:
        results = self.sync_range(start, end, force=force)
        return (
            self.store.trend(metric, start.isoformat(), end.isoformat()),
            [warning for result in results for warning in result.warnings],
        )

    def export_range(
        self, start: date, end: date, *, sync_first: bool = False
    ) -> tuple[list[dict], list[str]]:
        warnings: list[str] = []
        if sync_first:
            for result in self.sync_range(start, end, force=True):
                warnings.extend(result.warnings)
        return self.store.export_range(start.isoformat(), end.isoformat()), warnings

    def backfill(self, start: date, end: date, *, force: bool = False) -> BackfillResult:
        refreshed = skipped = failed = 0
        warnings: list[str] = []
        days = inclusive_days(start, end)
        auth_failed = False
        for day in days:
            try:
                result = self.sync_day(day, force=force)
            except Exception as exc:
                failed += 1
                warnings.append(f"sync for {day.isoformat()} failed: {type(exc).__name__}")
                if mfp_client.is_auth_error(exc):
                    auth_failed = True
                    break
                continue
            if result.refreshed:
                refreshed += 1
            else:
                skipped += 1
            warnings.extend(result.warnings)
        # Measurements are range-oriented upstream data, so archive them once
        # after per-day diary commits rather than issuing one request per day.
        if not auth_failed:
            self._progress(
                f"Fetching weight measurements for {start.isoformat()} through {end.isoformat()}..."
            )
            try:
                self.store.persist_weights(
                    self.acquirer.fetch_weights(start, end), days
                )
                self._progress("Archived weight measurements.")
            except Exception as exc:
                if mfp_client.is_auth_error(exc):
                    failed += 1
                warnings.append(f"weight measurements failed: {type(exc).__name__}")
        return BackfillResult(
            requested=len(days), refreshed=refreshed, skipped=skipped,
            failed=failed, warnings=warnings,
        )
