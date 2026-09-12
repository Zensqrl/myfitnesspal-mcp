"""Optional output boundary; no publisher is required for core operation."""

from __future__ import annotations

from typing import Protocol

from .models import ServiceDayResult


class NutritionPublisher(Protocol):
    def publish_current_state(self, result: ServiceDayResult) -> None:
        """Publish a committed current-state snapshot."""


class NullPublisher:
    def publish_current_state(self, result: ServiceDayResult) -> None:
        return None
