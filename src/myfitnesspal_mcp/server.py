import asyncio
import datetime
import math
import threading
from typing import Any, Callable

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from . import auth, config, diary, mfp_client, refresh
from .service import NutritionService
from .store import Store, trend_column

mcp = FastMCP("myfitnesspal")

_store: Store | None = None
_store_account: str | None = None
_store_lock = threading.RLock()
_stores: dict[str | None, Store] = {}


def get_store(username: str | None = None) -> Store:
    """Return the cache for exactly one account, or the legacy cache if unknown."""
    global _store, _store_account
    account_id = config.account_key(username) if username else None
    with _store_lock:
        if _store is not None and _store_account == account_id:
            return _store
        if account_id in _stores:
            _store = _stores[account_id]
        else:
            path = config.database_path(username)
            _store = Store(path, account_id=account_id)
            _stores[account_id] = _store
        _store_account = account_id
        return _store


def _store_for_client(client: Any) -> Store:
    return get_store(client.effective_username)


def _run_session_op(op: Callable[[Store, Any], Any]) -> Any:
    client = mfp_client.get_client()
    return op(_store_for_client(client), client)


def parse_day(value: str | None) -> datetime.date:
    if value is None:
        return datetime.date.today()
    return datetime.date.fromisoformat(value)


def parse_range(
    start: str | None, end: str | None, span_days: int = 30
) -> tuple[datetime.date, datetime.date]:
    end_day = parse_day(end)
    if start is None:
        start_day = end_day - datetime.timedelta(days=span_days - 1)
    else:
        start_day = parse_day(start)
    if start_day > end_day:
        raise ValueError("start must be on or before end")
    return start_day, end_day


async def _info(ctx: Context | None, message: str) -> None:
    if ctx is not None:
        await ctx.info(message)


async def run_with_refresh(ctx: Context | None, op: Callable[[], Any]) -> Any:
    """Runs a blocking MFP operation; on an auth-shaped failure, notifies the
    client, refreshes the session (headless browser profile when available,
    otherwise re-reads MFP_COOKIE / cookies.json), and retries once."""
    try:
        return await asyncio.to_thread(op)
    except Exception as exc:
        if not mfp_client.is_auth_error(exc):
            raise
        await _info(ctx,
            "MyFitnessPal rejected the session — refreshing credentials and retrying."
        )
        try:
            await asyncio.to_thread(refresh.refresh_session)
            result = await asyncio.to_thread(op)
        except Exception as retry_exc:
            await _info(ctx, "Session refresh failed.")
            raise RuntimeError(
                f"{mfp_client.RECONNECT_HINT} (retry after refresh failed)"
            ) from retry_exc
        await _info(ctx, "Session refreshed; the retried call succeeded.")
        return result


async def with_session(ctx: Context | None, op: Callable[[Store, Any], Any]) -> Any:
    """Runs `op` against the store and a live MFP client, re-resolving both on
    the retry so a refreshed session is picked up."""
    return await run_with_refresh(
        ctx, lambda: _run_session_op(op)
    )


def _required_text(value: str, name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value.strip()


def _positive_finite(value: float, name: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value


async def _refresh_after_write(store: Store, client: Any, day: datetime.date) -> list[str]:
    """Best-effort cache repair; a refresh failure cannot undo a remote write."""
    try:
        result = await asyncio.to_thread(
            NutritionService(store).get_day, day, force=True
        )
        return result.warnings
    except Exception:
        return ["MyFitnessPal was updated, but the local cache refresh failed"]


async def _write_result(
    ctx: Context | None,
    prepare: Callable[[Store, Any], Any],
    commit: Callable[[Any, Any], dict],
    day: datetime.date | None = None,
) -> tuple[dict, Store]:
    store, client, prepared = await with_session(
        ctx, lambda store, client: (store, client, prepare(store, client))
    )
    # Deliberately no auth retry here: once submission starts, replay may duplicate it.
    result = await asyncio.to_thread(commit, client, prepared)
    response = {"ok": True, **result}
    if day is not None:
        warnings = await _refresh_after_write(store, client, day)
        if warnings:
            response["warnings"] = warnings
    return response, store


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def fitness_get_day(date: str | None = None, ctx: Context = None) -> dict:
    """Nutrition summary, diary entries, the MyFitnessPal daily note, and the
    local feel note for a day.

    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    service = NutritionService(get_store(auth.saved_username()))
    result = await asyncio.to_thread(service.get_day, day)
    response = result.data
    if result.warnings:
        response["warnings"] = result.warnings
    return response


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def fitness_search_food(
    query: str, limit: int = 5, with_macros: bool = True, ctx: Context = None
) -> dict:
    """Search MyFitnessPal's food database and return candidate matches.

    Each candidate has name, brand, calories, macros, serving, and the
    food_id + weight_id to pass to fitness_log_food to log exactly that item.
    """

    query = _required_text(query, "query")
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")

    def op(store, client):
        return {
            "query": query,
            "results": diary.search_food(client, query, limit, with_macros),
        }

    return await with_session(ctx, op)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
async def fitness_log_food(
    query: str,
    meal: str = "breakfast",
    quantity: float = 1.0,
    date: str | None = None,
    food_id: str | None = None,
    weight_id: str | None = None,
    ctx: Context = None,
) -> dict:
    """Log a food to the real MyFitnessPal diary.

    Searches for `query` and logs the top match. To log an exact item, pass
    the food_id + weight_id of a fitness_search_food candidate (query is then
    used as the display name). meal: breakfast|lunch|dinner|snacks.
    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)
    query = _required_text(query, "query")
    meal = diary.normalize_meal(meal)
    quantity = _positive_finite(quantity, "quantity")
    if (food_id is None) != (weight_id is None):
        raise ValueError("food_id and weight_id must be supplied together")
    if food_id is not None:
        food_id = _required_text(food_id, "food_id")
        weight_id = _required_text(weight_id, "weight_id")
    result, store = await _write_result(
        ctx,
        lambda store, client: diary.prepare_food(
            client, day, meal, query, quantity, food_id, weight_id
        ),
        diary.commit_food,
        day,
    )
    result["day"] = store.day_record(day.isoformat())
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True))
async def fitness_delete_food(
    query: str, meal: str | None = None, date: str | None = None, ctx: Context = None
) -> dict:
    """Remove a food from the MyFitnessPal diary by name match.

    query: text matched against logged entry names (e.g. "banana"). If this
    matches nothing, the error lists what's actually logged that day/meal — use
    that to retry with a better query. If it matches more than one entry (and
    none is an exact name match), the error lists the candidates; narrow `query`
    to pick one.
    meal: optional breakfast|lunch|dinner|snacks to disambiguate duplicates.
    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)
    query = _required_text(query, "query")
    meal = diary.normalize_meal(meal, optional=True)

    def prepare(store, client):
        doc, token = diary.diary_page(client, day)
        return {"entry": diary.resolve_entry(diary.diary_entries(doc), query, meal, day), "token": token}

    result, _ = await _write_result(
        ctx, prepare,
        lambda client, item: (
            diary.remove_entry(client, item["entry"]["entry_id"], item["token"])
            or {"removed": item["entry"]["name"], "meal": item["entry"]["meal"]}
        ),
        day,
    )
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True))
async def fitness_modify_food(
    query: str,
    new_query: str | None = None,
    meal: str = "breakfast",
    quantity: float = 1.0,
    date: str | None = None,
    ctx: Context = None,
) -> dict:
    """Replace a MyFitnessPal diary entry: deletes the match, then adds a food.

    query: the existing entry to replace (name match) within `meal`. If this
    matches nothing, the error lists what's actually logged that day/meal — use
    that to retry with a better query. If it matches more than one entry (and
    none is an exact name match), the error lists the candidates; narrow `query`
    to pick one.
    new_query: the food to add instead; omit to re-add `query` (e.g. to change
    quantity). meal: breakfast|lunch|dinner|snacks. date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)
    query = _required_text(query, "query")
    if new_query is not None:
        new_query = _required_text(new_query, "new_query")
    meal = diary.normalize_meal(meal)
    quantity = _positive_finite(quantity, "quantity")
    result, _ = await _write_result(
        ctx,
        lambda store, client: diary.prepare_modify_food(
            client, day, meal, query, new_query, quantity
        ),
        diary.commit_modify_food,
        day,
    )
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def fitness_log_weight(
    weight: float, date: str | None = None, ctx: Context = None
) -> dict:
    """Log a weight measurement to MyFitnessPal.

    weight: in your MyFitnessPal account's display unit (kg or lbs).
    Logging twice for the same date updates that day's measurement.
    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)
    weight = _positive_finite(weight, "weight")
    result, store = await _write_result(
        ctx, lambda store, client: None,
        lambda client, prepared: diary.set_weight(client, day, weight),
    )
    try:
        store.upsert_nutrition(day.isoformat(), weight=result["weight"])
    except Exception:
        result["warnings"] = ["MyFitnessPal was updated, but the local cache update failed"]
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def fitness_get_exercise(date: str | None = None, ctx: Context = None) -> dict:
    """Read the MyFitnessPal exercise diary (cardio + strength) for a day.

    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op(store, client):
        return diary.get_exercise(client, day)

    return await with_session(ctx, op)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def fitness_get_note(date: str | None = None, ctx: Context = None) -> dict:
    """Read the MyFitnessPal daily diary note (the free-text 'Notes' box at the
    bottom of the day) straight from your account.

    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    result = await asyncio.to_thread(
        NutritionService(get_store(auth.saved_username())).get_day, day
    )
    response = {"day": day.isoformat(), "note": result.data["note"]}
    if result.warnings:
        response["warnings"] = result.warnings
    return response


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True))
async def fitness_log_note(
    text: str, date: str | None = None, append: bool = False, ctx: Context = None
) -> dict:
    """Write the MyFitnessPal daily diary note (the free-text 'Notes' box at the
    bottom of the day). This is your real MFP note, synced to your account —
    distinct from the local-only fitness_log_feel.

    text: the note body. append: add to the existing note on a new line instead
    of replacing it. date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)
    result, store = await _write_result(
        ctx,
        lambda store, client: diary.prepare_note(client, day, text, append),
        diary.commit_note,
    )
    try:
        store.set_note(day.isoformat(), result["note"])
    except Exception:
        result["warnings"] = ["MyFitnessPal was updated, but the local cache update failed"]
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def fitness_log_feel(
    note: str | None = None, rating: int | None = None, date: str | None = None
) -> dict:
    """Save a 'how I feel today' note. Stored locally only — never sent to
    MyFitnessPal.

    rating: optional 1-5. date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)
    if rating is not None and not 1 <= rating <= 5:
        raise ValueError("rating must be between 1 and 5")
    return get_store(auth.saved_username()).set_feel(day.isoformat(), note, rating)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def fitness_get_trends(
    metric: str, start: str | None = None, end: str | None = None, ctx: Context = None
) -> dict:
    """A single metric over a date range, for charts/analysis.

    metric: weight | calories_in | protein | carbs | fat.
    start/end: YYYY-MM-DD (default: last 30 days).
    Returns {metric, points: [{day, value}, ...]} with nulls omitted.
    """
    trend_column(metric)
    start_day, end_day = parse_range(start, end)

    service = NutritionService(get_store(auth.saved_username()))
    points, warnings = await asyncio.to_thread(
        service.get_trend, metric, start_day, end_day
    )
    result = {"metric": metric, "points": points}
    if warnings:
        result["warnings"] = warnings
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def fitness_bulk_export(
    start: str | None = None, end: str | None = None, sync_first: bool = False,
    ctx: Context = None,
) -> dict:
    """Export a whole date range at once for analysis: per-day nutrition
    totals, food entries with macros, the MyFitnessPal daily note, and local
    feel notes. Read-only.

    start/end: YYYY-MM-DD (default: last 30 days ending today).
    sync_first: gap-fill from MyFitnessPal before exporting. Off by default so
    large historical exports stay fast on cached data.
    """
    start_day, end_day = parse_range(start, end)

    def op():
        service = NutritionService(get_store(auth.saved_username()))
        days, warnings = service.export_range(
            start_day, end_day, sync_first=sync_first
        )
        result = {
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
            "count": len(days),
            "days": days,
        }
        if warnings:
            result["warnings"] = warnings
        return result

    return await asyncio.to_thread(op)
