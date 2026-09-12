# Local nutrition data service

`mfp-mcp` treats MyFitnessPal as an upstream source and SQLite as the durable
archive. The same `NutritionService` is used by MCP tools, command-line sync,
historical backfill, and direct Python callers.

## Boundaries

- `acquisition.py` adapts the existing cookie-backed MyFitnessPal client and
  parser into secret-free `AcquiredDay` and `AcquiredFoodEntry` models.
- `service.py` owns freshness decisions, retry behavior, synchronization, and
  archive-first reads.
- `store.py` owns SQLite migrations and transactional persistence.
- `server.py` is a thin asynchronous MCP adapter.
- `cli.py` calls the service directly; it does not call an MCP client.
- `publishers.py` defines an optional current-state publishing protocol. No
  MQTT or Home Assistant dependency is required today.

## SQLite archive and migration

The database is migrated non-destructively when opened. It keeps the original
day nutrition, diary entries, notes, feel notes, and account binding, then
adds the archive fields below:

- `day_nutrition`: common nutrients, complete nutrient/goal JSON, retrieval
  metadata, and source hash.
- `diary_entry`: deterministic entry identity, source position, quantity,
  serving information, timestamps, and complete nutrient JSON.
- `raw_daily_data`: append-only, sanitized snapshots with payload hash and
  parser version.
- `sync_state`: attempts, successes, status, sanitized error, and source hash
  per day/component.

SQLite uses WAL mode, foreign keys, and a five-second busy timeout. A successful
daily retrieval writes normalized nutrition, entries, note data when available,
the raw snapshot, and sync metadata in one transaction. If that transaction
fails, the previous archived day remains intact.

The raw snapshot is a minimally transformed representation exposed by the
existing `python-myfitnesspal` parser. It retains all dynamically tracked
nutrients, goals, meals, entries, water, completion state, and note content
when retrieved. It intentionally excludes cookies, access tokens, CSRF values,
and HTTP headers. It is not a verbatim HTML capture.

## Freshness

By default, today is refreshed on every request, yesterday and other dates in
the last 30 calendar days are reconciled after 24 hours, and older archived
dates are immutable unless forced. A missing date is fetched regardless of its
age. If a refresh fails and a prior archived day exists, the service returns
that archived data with a warning; it never deletes valid data because an
upstream request failed.

Use `--force` for any CLI sync or backfill command when deliberately replacing
an immutable or otherwise fresh day.

## Scheduling and backfill

Examples suitable for cron, systemd timers, or a scheduled Docker invocation:

```bash
mfp-mcp sync today
mfp-mcp sync recent --days 7
mfp-mcp sync date 2024-06-14
mfp-mcp sync range 2024-06-01 2024-06-30
mfp-mcp backfill 2020-01-01 2024-12-31
```

Backfill processes days sequentially. It skips already archived immutable days,
commits each successful day independently, records failures, and can be safely
rerun after interruption. It exits nonzero if any requested day failed.

## Request pacing

Every MyFitnessPal HTTP request passes through one process-wide rate limiter,
including request retries. Defaults are six requests per minute, burst one, and
up to two seconds of random jitter. This is intentionally conservative for an
unofficial upstream interface. Change the following only with care:

```text
MFP_RATE_LIMIT_REQUESTS_PER_MINUTE=6
MFP_RATE_LIMIT_BURST=1
MFP_RATE_LIMIT_JITTER_SECONDS=2
```

Set requests per minute to `0` only to deliberately disable pacing, such as in
a controlled test environment. Retry backoff is configured independently with
`MFP_RETRY_ATTEMPTS` and `MFP_RETRY_BACKOFF_SECONDS`.

## Persistent deployments and backups

For Docker, mount durable storage at `/data` and set:

```text
MFP_DATABASE_PATH=/data/mfp.sqlite3
```

Alternatively, set `MFP_MCP_DATA_DIR=/data` to preserve the existing
account-scoped directory structure. Back up the database with SQLite's backup
API or a consistent copy while the process is stopped; include WAL sidecar files
when copying a live database conventionally.

## Direct Python use and future publishers

```python
from datetime import date
from myfitnesspal_mcp.runtime import create_service

service = create_service()
try:
    result = service.get_day(date(2024, 6, 14))
finally:
    service.store.close()
```

Future MQTT/Home Assistant support belongs behind `NutritionPublisher`. A
publisher can emit retained current values—today's calories and macros, last
food entry, and synchronization health—only after the SQLite transaction has
committed. Detailed historical data remains in SQLite rather than being mirrored
into Home Assistant state.
