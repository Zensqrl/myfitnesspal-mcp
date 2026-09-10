# AGENTS.md

## Project overview

This repository contains `mfp-mcp`, an unofficial Python MCP server for
MyFitnessPal. It exposes tools for reading and modifying a user's food diary,
logging weight and notes, and querying locally cached nutrition history.

MyFitnessPal does not provide a public API for these operations. The project
combines `python-myfitnesspal` reads with reverse-engineered web requests and a
`curl_cffi` Chrome fingerprint. Assume upstream HTML, CSRF behavior, and private
endpoints can change without notice.

## Repository map

- `src/myfitnesspal_mcp/server.py`: FastMCP tool definitions, date/range
  validation, thread offloading, read-operation authentication retry behavior,
  and safe write completion handling.
- `src/myfitnesspal_mcp/diary.py`: MyFitnessPal HTML parsing and diary/note/
  weight read-write requests.
- `src/myfitnesspal_mcp/mfp_client.py`: cookie-backed client construction,
  Cloudflare-compatible HTTP session, client caching, and auth-error detection.
- `src/myfitnesspal_mcp/sync.py`: component-aware diary cache synchronization,
  including explicit date ranges and a 24-hour historical-component TTL.
- `src/myfitnesspal_mcp/store.py`: SQLite schema, persistence/query methods,
  and cache-account binding safeguards.
- `src/myfitnesspal_mcp/auth.py`: cookie parsing, validation, and local storage.
- `src/myfitnesspal_mcp/refresh.py`: optional Playwright session refresh.
- `src/myfitnesspal_mcp/config.py`: environment variables and platform-specific
  config/data paths.
- `src/myfitnesspal_mcp/cli.py`: `serve`/`auth`/`migrate-cache` CLI and
  stdio/HTTP transport.
- `tests/fixtures/`: synthetic MyFitnessPal HTML used by parser tests.
- `server.json`: MCP Registry package metadata.

## Development workflow

Use Python 3.10 or newer and `uv` from the repository root.

```bash
uv sync --extra autorefresh
uv run pytest
```

The optional `autorefresh` extra is only needed for Playwright-backed session
refresh. The ordinary test suite does not need a MyFitnessPal account, live
cookie, browser, or network access.

Run a focused test while iterating, then the full suite before handing off:

```bash
uv run pytest tests/test_diary.py
uv run pytest tests/test_diary.py::test_food_search_parses_results_and_csrf
uv run pytest
```

There is currently no repository-configured formatter, linter, or type checker.
Follow the existing style: four-space indentation, type hints on public and
non-obvious interfaces, small functions, and descriptive pytest test names.

## Implementation rules

- Keep MCP-facing operations in `server.py` and domain/transport details in the
  corresponding module. Do not put scraping or SQL directly in tool functions.
- MyFitnessPal calls are blocking. MCP async reads must route them through
  `with_session()` / `run_with_refresh()` so they execute via
  `asyncio.to_thread` and can receive one auth-refresh retry.
- Do not replay a mutation after its remote submission begins: its outcome may
  be unknown and a replay can duplicate it. Preparation before submission may
  use the normal read retry path. A confirmed mutation may return `warnings`
  when its best-effort cache update fails.
- Refresh retries must rebuild or re-fetch the cached client after credentials
  change. Do not capture a stale client outside the retried operation.
- Treat authentication failures differently from parse or per-day failures.
  Auth-shaped errors must propagate to the refresh layer; expected sync
  failures may be logged and skipped by `tolerating_failures()`.
- Preserve cache behavior: the default window is exactly 30 calendar days
  including today; today is re-fetched; historical nutrition/diary, note, and
  measurement components are fresh for 24 hours when complete. Explicit-range
  reads must evaluate every requested day rather than falling back to a
  lookback window. `fitness_bulk_export(sync_first=True)` deliberately forces
  that whole range to refresh.
- Keep cache data account-safe. Never silently claim a legacy unbound cache or
  overwrite a different account's cache. The explicit
  `mfp-mcp migrate-cache --username NAME` path copies the legacy cache into the
  selected account cache and leaves the original unchanged.
- Use parameterized SQL for values. If adding a dynamic column or field, update
  and validate it through the explicit allowlists in `store.py`.
- Validate user-facing enums and ranges close to the MCP boundary. Continue to
  accept both `snack` and `snacks` where meal names are accepted.
- When changing scraped markup or private endpoint payloads, update the relevant
  fixture and add assertions for URL, method, headers, CSRF token, and payload.
- Keep HTTP mode bound to `127.0.0.1` by default. It has no built-in
  authentication and must not be made publicly reachable by default.

## Testing expectations

- Tests must be deterministic and offline. Use `FakeClient`, `FakeSession`, and
  `FakeResponse` from `tests/conftest.py` rather than a real account.
- Use `tmp_path` for cookies, SQLite databases, and browser/profile state.
- Cover success and failure paths for network-facing changes, especially auth
  failures, ambiguous diary matches, malformed/missing HTML data, non-2xx
  write responses, uncertain submission outcomes, and partial multi-step
  mutations.
- For store changes, test partial updates, ordering, null handling, and the union
  of all data sources returned by exports.
- If a feature changes the public MCP behavior, update tool docstrings and the
  tool table/configuration guidance in `README.md` in the same change.

## Credentials and generated data

Never commit or print session cookies, full Cookie headers, access tokens,
browser profiles, local databases, `.env` files, or real diary data. The
`MFP_COOKIE` value and `cookies.json` are secrets. Keep fixtures synthetic and
inspect staged changes for accidental credentials before committing.

Credential writes must stay atomic. On Windows, fail closed if owner-only ACLs
cannot be applied to credentials or newly created application directories; on
POSIX retain the corresponding restrictive file and directory modes.

The repository ignores `.venv/`, `*.db`, `cookies*`, `.env*` except
`.env.example`, build output, pytest caches, and `uv.lock`. Do not rely on
ignored local state when writing tests or documentation.

## Versioning and release metadata

CI runs pytest on Python 3.10 and 3.13. When changing the package version, keep
all three values synchronized:

- `project.version` in `pyproject.toml`
- top-level `version` in `server.json`
- `packages[0].version` in `server.json`

A version change pushed to `main` can trigger tag creation and publication to
PyPI and the MCP Registry. Do not bump versions or alter publishing workflow
credentials/permissions unless the task explicitly calls for a release change.
