"""Portable data models for archived MyFitnessPal diary data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class AcquiredFoodEntry:
    meal: str
    name: str
    nutrients: dict[str, float | None]
    quantity: float | None = None
    serving_description: str | None = None
    source_identifier: str | None = None
    timestamp: datetime | None = None


@dataclass(frozen=True)
class AcquiredDay:
    day: date
    totals: dict[str, float | None]
    goals: dict[str, float | None]
    entries: list[AcquiredFoodEntry]
    water_ml: float | None
    note: str | None
    note_retrieved: bool
    complete: bool
    raw_payload: dict[str, Any]
    retrieved_at: datetime
    parser_version: str = "1"


@dataclass(frozen=True)
class ServiceDayResult:
    data: dict[str, Any]
    source: str
    retrieved_at: datetime | None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SyncResult:
    day: date
    source: str
    refreshed: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BackfillResult:
    requested: int
    refreshed: int
    skipped: int
    failed: int
    warnings: list[str] = field(default_factory=list)
