# myfitnesspal-mcp

Connect MyFitnessPal to Claude or any MCP client. Log meals by talking, search
the food database with macros, track trends, and export nutrition history from
your real MyFitnessPal diary.

Published on PyPI as [`mfp-mcp`](https://pypi.org/project/mfp-mcp/).

<!-- mcp-name: io.github.Mason-Levyy/mfp-mcp -->

> **Unofficial.** MyFitnessPal has no public API. This server uses web app
> endpoints that can change at any time. Use it with your own account and at
> your own risk.

![quick demo](demo.gif)

## Quickstart

1. Connect your account once by pasting its session cookie:

   ```bash
   uvx mfp-mcp auth
   ```

2. Add the server to your client.

   **Claude Code**

   ```bash
   claude mcp add myfitnesspal -- uvx mfp-mcp
   ```

   **Claude Desktop** (`claude_desktop_config.json`)

   ```json
   {
     "mcpServers": {
       "myfitnesspal": {
         "command": "uvx",
         "args": ["mfp-mcp"]
       }
     }
   }
   ```

Requires [uv](https://docs.astral.sh/uv/). Any MCP client supporting stdio or
streamable HTTP works.

### Run a local checkout

`uvx mfp-mcp` runs the published package, not uncommitted local fixes. To run
this checkout instead, use:

```bash
uv --directory <checkout> run mfp-mcp
# Example:
uv --directory D:/github_personal/MyFitnessPal-mcp run mfp-mcp
```

For an MCP client, set the command to `uv` and its arguments to
`--directory`, `<checkout>`, `run`, `mfp-mcp` (use the example path above when
applicable). Run `uv --directory <checkout> run mfp-mcp auth` for local guided
authentication.

## Authentication

MyFitnessPal no longer supports the old headless password login. Use a browser
session cookie instead:

1. Log in at [myfitnesspal.com](https://www.myfitnesspal.com).
2. Open DevTools (F12), then **Application** (Chrome) or **Storage**
   (Firefox), then **Cookies** for `https://www.myfitnesspal.com`.
3. Copy the value of `__Secure-next-auth.session-token`.
4. Paste it into the `mfp-mcp auth` prompt. Input is hidden in a terminal.

A complete `Cookie:` header from the Network tab also works. Saved credentials
are written atomically. The cookie file is restricted to the current user,
including a Windows ACL; application-created config, account, and profile
directories are also private. Use `MFP_COOKIE` instead if you do not want a
saved cookie.

Sessions last about 30 days. Re-run `auth` when one expires, or enable
auto-refresh.

### Auto-refresh

With the `autorefresh` extra, `auth` seeds a persistent headless browser
profile. If MyFitnessPal rejects a read, the server refreshes the session,
saves the cookie, and retries that read once.

Submitted mutations are deliberately never replayed: retrying a food, weight,
or note submission could duplicate or otherwise alter a real diary entry.
Preparation before submission can use the normal authenticated read path.

```bash
uvx --from 'mfp-mcp[autorefresh]' playwright install chromium
uvx --from 'mfp-mcp[autorefresh]' mfp-mcp auth
```

Use the same `--from 'mfp-mcp[autorefresh]'` form in your client configuration.

## Tools

| Tool | What it does |
| --- | --- |
| `fitness_get_day` | Nutrition totals, diary entries, MFP daily note, and feel note for one day |
| `fitness_search_food` | Candidate matches with brand, calories, macros, serving, and IDs |
| `fitness_log_food` | Log a food to the real diary (top match or exact search candidate) |
| `fitness_delete_food` | Remove a diary entry by name match |
| `fitness_modify_food` | Replace an entry or change its quantity |
| `fitness_log_weight` | Log a weight measurement (updates the same day on re-log) |
| `fitness_get_exercise` | Read the exercise diary (cardio and strength) |
| `fitness_get_note` | Read the MyFitnessPal daily diary note |
| `fitness_log_note` | Write that daily note (replace or append) |
| `fitness_log_feel` | Save a subjective local-only note |
| `fitness_get_trends` | One metric over a date range: weight, calories_in, protein, carbs, fat |
| `fitness_bulk_export` | Export a whole date range for analysis |

For high-accuracy logging, call `fitness_search_food("greek yogurt")`, then
pass the selected candidate's `food_id` and `weight_id` to
`fitness_log_food`. This avoids relying on the top search result.

### Write results and errors

After a confirmed MyFitnessPal write, the server repairs its local cache
best-effort. A successful response can include `ok: true` and a `warnings`
array when that cache repair fails; the remote update still succeeded.

An error saying the submission outcome is **uncertain** means a transport
failure happened after a submission started. Check MyFitnessPal before trying
again. Replacing a food is a delete-then-add operation and can report a
**partial** mutation when removal completed but the replacement add failed.

Water appears in day summaries but is read-only. MyFitnessPal's known
`/food/water` POST does not persist changes.

## Cache synchronization

Day summaries, trends, and exports use a local SQLite cache. The default sync
range is exactly 30 calendar days including today. `fitness_get_day` refreshes
only the day requested. Trend requests evaluate their explicit inclusive
`start`/`end` range; if omitted, the default is the last 30 days ending today.

Today is refreshed live. Complete historical nutrition/diary, note, and
measurement components are reused for up to 24 hours. Their completion is
tracked separately, but a stale diary/nutrition or note component refreshes
that day's nutrition/diary and note fetches together.
`fitness_bulk_export(sync_first=true)` forces every day in its explicit
inclusive range to refresh; leaving `sync_first` false exports only what is
already cached.

### Cache accounts and legacy migration

Cache and browser-profile storage are account-scoped so one account's history
is not reused for another. Existing unbound legacy `data.db` files are never
claimed implicitly. To copy one into the selected account cache, run:

```bash
mfp-mcp migrate-cache --username NAME
```

`NAME` is your MyFitnessPal username. This explicit copy migration binds the
copied cache to the normalized account, preserves the legacy source, and
refuses to overwrite an existing account cache.

## Remote / HTTP mode

The default transport is stdio. For network clients:

```bash
mfp-mcp --http --host 127.0.0.1 --port 8484
```

This serves streamable HTTP at `/mcp`. There is no built-in authentication, so
never expose it directly to the internet. Keep it on localhost or place it
behind a VPN, authenticated reverse proxy, or OAuth-aware MCP gateway.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `MFP_COOKIE` | Session cookie (full header or bare token); overrides saved credentials | none |
| `MFP_USERNAME` | MyFitnessPal username, not email; used if profile lookup fails | auto-detected |
| `MFP_IMPERSONATE` | curl_cffi browser fingerprint; try `chrome124` for some 403s | `chrome` |
| `MFP_REQUEST_TIMEOUT` | Finite per-request HTTP timeout in seconds, greater than 0 and at most 300 | `30` |
| `MFP_SYNC_DAYS` | Default gap-fill lookback in days | `30` |
| `MFP_MCP_DATA_DIR` | Root for local SQLite caches and browser profiles | platform data directory |

When `MFP_MCP_DATA_DIR` points to a directory you chose, the server does not
change that root directory's ACL or permissions. It does secure
application-created subdirectories.

## Troubleshooting

- **403 / Cloudflare blocked:** Try `MFP_IMPERSONATE=chrome124` or another
  supported [curl_cffi target](https://github.com/lexiforest/curl_cffi#supported-browsers).
- **Session expired:** Re-run `mfp-mcp auth`, or install the auto-refresh extra.
- **Profile lookup fails:** Set `MFP_USERNAME` to your username, not email.

## Development

```bash
git clone https://github.com/zensqrl/MyFitnessPal-mcp
cd myfitnesspal-mcp
uv sync --extra autorefresh
uv run pytest
```

Tests use synthetic MyFitnessPal HTML and JSON fixtures. No account is needed.

## License

[MIT](LICENSE)
