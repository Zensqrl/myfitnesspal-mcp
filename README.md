# myfitnesspal-mcp

### Diary username correction

When MyFitnessPal's profile lookup fails, an email-address fallback can route diary reads to the wrong page. An explicitly verified username can be pinned with `scripts/set-diary-username.py` inside the app container. The script checks the saved authenticated principal against the archive binding and writes only `diary-username.json` in that account's data directory. The client checks that binding on every routed diary request. Credentials and archive identity are not renamed or migrated; friend-diary requests are unaffected. Remove that routing file to revert the override. Do not infer a username from an email address.

### Setup activity panel (LAN deployment)

The setup page shows the latest 100 timestamped activity messages and refreshes every 2.5 seconds. Messages cover queue acceptance/rejection, authentication, cache checks, upstream fetches, retries, and completion/incomplete results. This is a sanitized in-memory activity feed, not a shell console; it clears on app restart and never includes tokens or diary contents. `Ready` means idle, not proof of valid authentication or complete nutrition.

The setup page's **Sync today** requests a fresh fetch rather than reusing the 15-minute cache. Rejected requests explain why. A successful cookie import closes any old local browser lease. Missing nutrition totals/targets or a failed fetch returning stale cache produce **Sync incomplete**, not a successful completion message. The existing MCP read freshness policy is unchanged.

For an app-only Ubuntu update after transferring source into `/opt/myfitnesspal-mcp/app`, run `scripts/update-app.py` with Python 3. It rebuilds/recreates only the app and rolls back the image if health verification fails; browser/gateway and persistent mounts are retained.

### Setup archive utilities

The authenticated HTTPS setup page includes three local archive utilities:

- **Backfill from a date** synchronizes every date from the selected start through the server's current date.
- **Sync a date range** synchronizes both selected endpoints and every date between them.
- **Archive day viewer** reads one canonical database record without contacting MyFitnessPal and renders the same response as rich text or formatted JSON.

Historical synchronization skips dates whose `nutrition_diary` component is already marked complete. Selecting **Force resync completed dates** retrieves every requested date. Each successful date commits independently, non-authentication failures do not erase older cached data, and rerunning a partially completed job resumes by skipping successes. An authentication failure stops the range and requires reconnection. Weight measurements are fetched once per range when at least one diary date is refreshed (or when force is selected).

Only one upstream operation runs at a time. Progress reports processed, refreshed, skipped and failed counts. **Cancel after current date** stops between requests; it does not interrupt or roll back the date already in flight. Large or forced ranges require browser confirmation and still use the shared upstream rate limiter.

The archive viewer returns the current canonical day record plus sanitized sync metadata. It does not expose credential data, account-binding metadata, internal hashes or every stored raw snapshot. Food names and notes are shown because this is an administrator-authenticated archive view; browser code inserts them as text, never HTML.

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

## Headless Ubuntu deployment with browser setup

The `feature/browser-onboarding` deployment runs a private browser on Ubuntu
and displays it in a password-protected setup page. Sign into MyFitnessPal in
that browser, then select **Finish connecting**. No cookie extraction, SSH
session, or container restart is needed for subsequent logins.

All installation files and persistent state live beneath
`/opt/myfitnesspal-mcp`. This deployment serves a separate, archive-only MCP
tool set suitable for Home Assistant. The ordinary `serve`/stdio behavior and
its full tool set remain available independently.

### Scripted installation from Windows

Use the feature branch checkout and run the deployment wrapper:

```powershell
.\scripts\deploy.ps1 -SshTarget youruser@your-ubuntu-server
```

The wrapper uploads a source snapshot (including current feature changes) and
runs `install-ubuntu.sh` over an interactive SSH connection. SSH and sudo may
prompt for their passwords. It uploads only application source, deployment
files and scripts; local credentials and database files are excluded.

For optional SSH key setup, run `scripts/setup-ssh.ps1 -SshTarget youruser@your-ubuntu-server`
in your own PowerShell terminal. It creates a dedicated Ed25519 key, secures its
private file with a Windows ACL, and adds only its public key to the server's
authorized keys. Enter your server password locally when prompted. Existing
keys are preserved; the script changes neither sudo policy nor your password.
For unattended use, leave the key passphrase empty or load a protected key into
an SSH agent. Both `deploy.ps1` and `fetch-ca.ps1` accept `-IdentityFile` for this
key (default helper location: `$env:USERPROFILE/.ssh/id_ed25519_myfitnesspal_mcp`).

Alternatively, from a checkout already on Ubuntu:

```bash
sudo bash scripts/install-ubuntu.sh
```

The installer checks Ubuntu and Docker, installs Docker Engine and Compose
from Docker's Ubuntu package repository when missing, creates private
directories, and generates credentials. It asks for:

- The Ubuntu server's LAN IPv4 address and setup hostname (using the IP is fine).
- Home Assistant's IPv4 address; the default is `192.168.50.10`.
- Your administrator network CIDR, for example `192.168.50.0/24`.
- Your timezone; the default is `America/New_York`.
- A new setup-page administrator password.

Reserve the server and HA addresses in DHCP. The installer binds only the
chosen IPv4 address; it does not add an IPv6 listener or change your firewall.
It preserves existing deployment configuration, secrets, profiles and archives
on subsequent runs. A dirty Git checkout at the server destination is rejected.
Docker/Desktop must be running; an existing stopped engine is reported rather
than replaced. First builds need Internet access to download packages/images.

### HTTPS trust and first login

The setup address is `https://<setup-hostname-or-ip>:8443`; the username is
`admin`, with the password you chose during installation. Caddy generates a
private certificate authority for local HTTPS. No public domain is required.

Copy **only** this public certificate to your computer using your file-transfer
client or the script below; never copy/distribute the private `root.key`:

```powershell
.\scripts\fetch-ca.ps1 -SshTarget youruser@your-ubuntu-server
.\scripts\trust-ca.ps1 -CertificatePath .\mfp-root.crt
```

The trust helper displays the certificate fingerprint and asks for explicit
confirmation before trusting it for your Windows user. Verify it against the
fingerprint printed through SSH by `fetch-ca.ps1`. Firefox may require a
separate certificate import in its settings. On other clients, import the
public CA into that client's trusted certificate store before logging in.

The server browser opens `https://www.myfitnesspal.com/account/login` directly
with a 480×900 portrait viewport, mobile user agent and touch support. On a phone,
use the noVNC keyboard control if tapping a field does not open your keyboard.
After a browser-container update, cancel any old session and reconnect.

Open the setup page, click **Connect / reconnect**, sign into the actual
MyFitnessPal site, and click **Finish connecting**. Complete MFA or challenges
yourself. Browser sessions expire after 15 minutes. If a profile lookup fails,
you can supply your MyFitnessPal username; reconnecting an existing archive
with an unverified account identity is deliberately rejected.

Cookie paste remains a fallback if MyFitnessPal rejects the server browser.
Passwords, session cookies and browser profiles are never sent to Home
Assistant. Do not set `MFP_COOKIE` or `MFP_USERNAME` on the web service: saved
credentials are its source of truth.

**Disconnect** removes usable local credentials and the browser profile while
retaining the archive and its account identity for offline reads. It does not
revoke sessions on MyFitnessPal's servers. Connecting a different account
after disconnect creates/uses that account's separate archive.

### Home Assistant configuration

For an existing installation affected by a reboot-time LAN address race, run
`scripts/install-boot-recovery.ps1` from Windows and enter the Ubuntu sudo
password locally. New installs include this recovery timer automatically.
It retries the gateway's address-binding failure every 30 seconds and restores
a missing project-network attachment; it does not restart intentionally stopped
containers or alter Docker startup for unrelated services. A reboot verification
is still required after installing it.

In **Settings → Devices & services → Add integration**, choose **Model Context
Protocol** (the MCP *client*, not **Model Context Protocol Server**).

| Setting | Value |
| --- | --- |
| Server URL | `http://<ubuntu-lan-ip>:8484/mcp` — the installer prints the exact URL |
| Transport | Streamable HTTP, detected automatically |
| OAuth client ID / secret | Not needed for this IP-restricted LAN deployment |
| Permitted source address | The HA address supplied to the installer, default `192.168.50.10` |
| Setup page | `https://<setup-hostname-or-ip>:8443` — do not use this as the MCP URL |

Then configure your conversation agent to use the MyFitnessPal MCP tools in
its available LLM API/tool settings. The exact agent settings vary by provider.
Ask for today's archived calories and confirm the answer includes the data's
age. Adding the integration supplies tools to conversation agents; it does
not create nutrition sensors or dashboards.

Home Assistant 2026.9.2 supports Streamable HTTP and applies a 10-second
tool-call timeout. Some HA documentation/UI versions still say “SSE URL”; use
`/mcp`, not `/sse`, with this deployment. See the [integration documentation](https://www.home-assistant.io/integrations/mcp/)
and [2026.9.2 client implementation](https://github.com/home-assistant/core/blob/2026.9.2/homeassistant/components/mcp/coordinator.py).

| Browser-deployment tool | Behavior |
| --- | --- |
| `fitness_get_day(day)` | Returns the local day's nutrition, diary, notes and feel entry, retrieval timestamp, staleness and whether refresh was queued |
| `fitness_connection_status()` | Connection, reconnect requirement and background job status; no credentials |
| `fitness_sync_today()` | Queues today's sync and returns immediately |

The web deployment deliberately does not expose live food search or diary
mutations. Upstream rate limiting and interactive reauthentication cannot
reliably fit HA's tool-call timeout. Missing/stale reads queue background work,
and a missing day is explicitly reported as not cached rather than as zero.
Today is refreshed in the background every 15 minutes; yesterday uses the
existing freshness policy. Queue entries are bounded and deduplicated. The
worker owns all upstream operations in one process so the shared rate limiter
continues to apply. A restart discards pending jobs; periodic sync and repeated
day queries safely enqueue them again.

The HA listener is **unencrypted HTTP protected by source-IP restriction**.
Use it only on a trusted LAN. It has no bearer token or OAuth login. The gateway
enforces the exact source IP and does not trust client-supplied forwarding
headers; only `/mcp` is available on that port. For networks requiring encrypted
MCP traffic, provision a certificate trusted by HA and change the gateway to
HTTPS before use. Do not expose either listener through Internet port forwarding.

### WSL testing

```powershell
.\scripts\setup-wsl.ps1
```

The helper prepares the Ubuntu distribution if missing, then invokes the same
Linux installer. Ubuntu's initial user creation and a Windows-required restart
cannot be completed silently; rerun the helper afterward. If using Docker
Desktop, start it and enable integration with that Ubuntu distribution first.
Otherwise the installer can install native Docker under a systemd-enabled
Ubuntu WSL distribution. Files are still stored in `/opt/myfitnesspal-mcp` on
the Linux filesystem.

WSL's default NAT address is not normally reachable from Home Assistant on
another machine. Use WSL for browser/backend tests; use the Ubuntu LAN server
for the final HA test, or configure WSL mirrored networking separately. Do not
use `localhost` as HA's server address: it would refer to HA itself.

### Verification, updates and backups

```bash
sudo bash /opt/myfitnesspal-mcp/app/scripts/verify-deployment.sh
sudo bash /opt/myfitnesspal-mcp/app/scripts/backup.sh
```

Verification checks the internal MCP handshake/tool list and rejects direct
backend access. Verify the external route by adding the integration from HA;
an internal smoke test cannot establish HA's network path or source address.
The browser can also be exercised without a real account:

```powershell
.\scripts\test-containers.ps1
```

For a native Ubuntu/WSL engine, the equivalent scripted path is:

```bash
sudo bash scripts/setup-test-engine.sh
sudo bash scripts/test-containers.sh
```

From Windows with the native Ubuntu WSL engine already prepared, use
`scripts/test-containers.ps1 -Wsl`. Linux/WSL testing also exercises the full
Compose stack with temporary state under `/opt/myfitnesspal-mcp/test-runs` and
loopback-only ports. It removes that synthetic state and stops its containers
when finished; built images are retained for subsequent tests.

That script builds the images and runs a synthetic login through real Chromium,
cookie harvesting, profile promotion, refresh, and disconnect. It uses only
temporary container storage and no MyFitnessPal credentials.

Re-run `deploy.ps1` to update from the feature checkout. The source snapshot is
installed under `app`; deployment settings and data live outside it. A backup
stops the app/browser briefly, uses SQLite's backup API, copies private state,
and restarts the services even if copying fails. Backups contain secrets and
private certificate keys: encrypt them before moving them off the host.

When the existing `mfp-mcp` container uses the legacy mounts beneath
`/opt/mcp-mfp/myfitnesspal-mcp/state`, installation builds the new images first,
stops that container, takes a private backup, and copies its config/archive to
the new location. The old container and original state are retained. Failed
startup/verification restarts the old container; successful migration disables
its automatic restart to free port 8484 across reboots. Unexpected mounts or
existing destination state cause migration to stop rather than overwrite data.
Environment-only MyFitnessPal cookies are not copied: use the new browser login
if the old deployment did not save credentials in its config directory.

### Deployment troubleshooting

- **403 from HA:** confirm HA's current source IP matches `MFP_HA_IP` in
  `/opt/myfitnesspal-mcp/config/deployment.env`; NAT can change the observed IP.
- **Certificate warning:** trust the public CA on your computer and use the
  configured hostname/IP; do not enter your password on an unverified origin.
- **Browser login rejected:** complete challenges manually, retry, or use the
  cookie fallback. Automatic refresh is best effort, not a fresh password login.
- **Reconnect required:** reconnect from the setup page; the archive remains
  readable while upstream authentication is unavailable.
- **Data not cached yet:** wait for the queued job, then query again. Backfills
  can take time under the conservative shared upstream rate limiter.
- **Browser sandbox error:** run `test-containers.ps1` before attempting real
  login. The deployment requires an environment that permits Chromium's
  sandbox; do not disable it or run the container privileged as a workaround.

See [the browser deployment design](docs/browser-onboarding.md) for service
boundaries, storage and lifecycle details.

### Run a local checkout

`uvx mfp-mcp` runs the published package, not uncommitted local fixes. To run
this checkout instead, use Python 3.13 explicitly (the version currently tested
by this project):

```bash
uv --directory <checkout> run mfp-mcp
# Example:
uv --directory D:/github_personal/MyFitnessPal-mcp run mfp-mcp
```

For an MCP client, set the command to `uv` and its arguments to
`--directory`, `<checkout>`, `run`, `--python`, `3.13`, `mfp-mcp` (use the
example path above when applicable). Run the following for local guided
authentication:

```bash
uv --directory <checkout> run mfp-mcp auth
```

Example:
```bash
uv --directory . run --python 3.13 mfp-mcp auth
```

When your shell is already in the checkout, `.` can replace `<checkout>`.
Running bare `mfp-mcp` may select an older copy installed in the active Python
or pyenv environment instead of this checkout.

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

## Local archive and synchronization

SQLite is the durable local archive. MCP tools and direct CLI jobs share the
same service, freshness policy, acquisition code, and transactional persistence.
Every successful upstream daily retrieval is archived before it is returned.
The archive retains normalized nutrition/food data plus sanitized, minimally
transformed raw snapshots, so historical data remains useful if MyFitnessPal
access later stops working. See [the data service guide](docs/data-service.md)
for the architecture, schema, backup guidance, and direct Python use.

Today is refreshed live by default. Yesterday and dates inside the configurable
30-day mutable window are reconciled after 24 hours. Cached dates older than
that window are returned locally without contacting MyFitnessPal unless forced.
If a refresh fails, prior archive data remains available with a warning.
`fitness_bulk_export(sync_first=true)` forces every day in its explicit range;
leaving it false exports only local data.

### Scheduled sync and historical backfill

The service can run without MCP or AI:

```bash
uv run mfp-mcp sync today
uv run mfp-mcp sync recent --days 7
uv run mfp-mcp sync date 2024-06-14
uv run mfp-mcp sync range 2024-06-01 2024-06-30
uv run mfp-mcp backfill 2020-01-01 2024-12-31
```

These commands intentionally execute the current checkout. If you installed a
release that contains these commands, the shorter `mfp-mcp ...` form is also
valid. Sync and backfill print progress to the terminal before each potentially
slow upstream fetch, retry, and archive update.

Backfill is sequential, resumable, idempotent, and uses conservative pacing for
every upstream request. It skips successfully archived immutable dates unless
`--force` is supplied, records failures, and exits nonzero when a requested day
cannot be synchronized.

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
| `MFP_DATABASE_PATH` | Optional explicit SQLite file; overrides data directory | none |
| `MFP_TODAY_TTL_SECONDS` | Freshness TTL for today; `0` refreshes every read | `0` |
| `MFP_YESTERDAY_TTL_HOURS` | Freshness TTL for yesterday | `24` |
| `MFP_RECENT_TTL_HOURS` | Freshness TTL inside mutable history | `24` |
| `MFP_MUTABLE_HISTORY_DAYS` | Cached dates older than this are immutable by default | `30` |
| `MFP_RATE_LIMIT_REQUESTS_PER_MINUTE` | Process-wide upstream request rate; `0` disables pacing | `6` |
| `MFP_RATE_LIMIT_BURST` | Maximum immediate request burst | `1` |
| `MFP_RATE_LIMIT_JITTER_SECONDS` | Random delay added to requests | `2` |
| `MFP_RETRY_ATTEMPTS` | Total attempts for transient sync failures | `2` |
| `MFP_RETRY_BACKOFF_SECONDS` | Initial retry delay; later attempts back off exponentially | `2` |
| `MFP_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` | `WARNING` |

When `MFP_MCP_DATA_DIR` points to a directory you chose, the server does not
change that root directory's ACL or permissions. It does secure
application-created subdirectories.

For Docker, mount a persistent volume at `/data` and set
`MFP_DATABASE_PATH=/data/mfp.sqlite3`. The existing account-scoped directory
layout also works with `MFP_MCP_DATA_DIR=/data`.

## Troubleshooting

- **403 / Cloudflare blocked:** Try `MFP_IMPERSONATE=chrome124` or another
  supported [curl_cffi target](https://github.com/lexiforest/curl_cffi#supported-browsers).
- **Session expired:** Re-run `mfp-mcp auth`, or install the auto-refresh extra.
- **Profile lookup fails:** Set `MFP_USERNAME` to your username, not email.

## Development

```bash
git clone https://github.com/zensqrl/MyFitnessPal-mcp
cd myfitnesspal-mcp
uv python pin 3.13
uv sync --extra autorefresh
uv run pytest
```

Tests use synthetic MyFitnessPal HTML and JSON fixtures. No account is needed.

## License

[MIT](LICENSE)
