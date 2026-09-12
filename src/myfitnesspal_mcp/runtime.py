"""Application composition for CLI and direct Python consumers."""

from __future__ import annotations

from . import auth, config
from .service import NutritionService
from .store import Store


def create_service(username: str | None = None) -> NutritionService:
    """Create a service using the configured account-scoped archive."""
    username = username or auth.saved_username()
    account_id = config.account_key(username) if username else None
    return NutritionService(
        Store(config.database_path(username), account_id=account_id)
    )
