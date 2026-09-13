"""One freshness policy shared by MCP, direct callers, and scheduled jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from . import config


@dataclass(frozen=True)
class FreshnessPolicy:
    today_ttl: timedelta
    yesterday_ttl: timedelta
    recent_ttl: timedelta
    mutable_history_days: int

    @classmethod
    def from_config(cls) -> "FreshnessPolicy":
        return cls(
            today_ttl=config.today_ttl(),
            yesterday_ttl=config.yesterday_ttl(),
            recent_ttl=config.recent_ttl(),
            mutable_history_days=config.mutable_history_days(),
        )

    def is_immutable(self, day: date, today: date) -> bool:
        return day < today - timedelta(days=self.mutable_history_days - 1)

    def ttl_for(self, day: date, today: date) -> timedelta | None:
        if self.is_immutable(day, today):
            return None
        if day == today:
            return self.today_ttl
        if day == today - timedelta(days=1):
            return self.yesterday_ttl
        return self.recent_ttl

    def requires_refresh(
        self,
        day: date,
        retrieved_at: datetime | None,
        *,
        today: date,
        now: datetime | None = None,
        force: bool = False,
    ) -> bool:
        if force or retrieved_at is None:
            return True
        ttl = self.ttl_for(day, today)
        if ttl is None:
            return False
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        return now - retrieved_at > ttl
