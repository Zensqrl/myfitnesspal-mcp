# Browser onboarding deployment

This is an opt-in deployment (`deploy/compose.yaml`) rather than a replacement
for the ordinary CLI, stdio server, or existing Compose configuration.

## Boundaries

- Caddy is the only service with published ports. Setup uses HTTPS/basic auth
  plus administrator-network restriction; HA uses an exact-source-IP HTTP rule.
- `web.py` serves local static assets, CSRF-checked commands and archive-only
  FastMCP tools. All backend routes except the nonsensitive health probe require
  the gateway's secret header. This also prevents Chromium from directly calling
  the setup API through an attacker-controlled page.
- `onboarding.py` owns a bounded job queue and one upstream executor. Login
  validation, credential changes, refresh and sync are serialized. Archive reads
  run outside that queue and never wait for upstream requests.
- `browser_service.py` owns Playwright on one dedicated thread. Its private API
  requires a separate bearer secret. Chromium profiles and cookie extraction
  never become public browser endpoints. Raw VNC binds to loopback; noVNC
  WebSocket handshakes require gateway authentication and an active login lease.

No container has a Docker socket, host networking, privileged mode or access to
the entire project state. The browser sees its own profile and private API key;
the app sees credentials and archive; the gateway sees TLS state and gateway
credentials. Chromium runs as a non-root user with its sandbox enabled.

## Profile lifecycle

Interactive login starts a fresh `candidate` profile. The old `active` profile
stays untouched until the candidate's cookies validate. A failed/cancelled or
expired login closes Chromium and removes only the candidate. On success,
credentials are saved atomically, then the browser closes and promotes the
candidate. If promotion fails, validated saved credentials still work; reconnect
to repair the browser profile. Startup cleanup removes an abandoned candidate.
Refresh cannot run while an interactive browser owns the profile.

Account identity is checked against the archive username and a private SHA-256
binding of the upstream account identifier. A profile-lookup fallback cannot
silently bind an existing archive to an unverified username. Disconnect keeps
the archive identity but removes usable cookies and browser profiles.

## Files

```text
/opt/myfitnesspal-mcp/
  app/                   source snapshot, scripts and Compose definition
  config/deployment.env  non-secret deployment parameters (private permissions)
  secrets/               administrator hash and internal API keys
  state/config/          atomic credentials file
  state/data/accounts/   account-scoped archives and principal bindings
  state/browser/         active/candidate browser profiles
  state/gateway/         Caddy certificates and private CA keys
  state/gateway-config/  Caddy runtime configuration
  backups/               private SQLite and state backups
```

The web service must run as a single process/worker. Do not scale replicas or
run CLI synchronization concurrently against its state: the limiter and job
queue are process-local. The HTTP endpoint is stateless at the MCP transport
layer; pending background jobs are intentionally not durable.

## Test boundaries

The unit suite uses synthetic data and never signs into MyFitnessPal. The real
Chromium smoke test uses a local fixture website and temporary profiles. Final
acceptance still requires a human MyFitnessPal login, an actual HA connection,
and a host restart test on the chosen Ubuntu server. These cannot be inferred
from successful unit tests or a local browser fixture.
