# Personal Dashboard v0.1 — MyFitnessPal MCP handoff

Last updated: 2026-09-20 (America/New_York)
Repository: https://github.com/zensqrl/MyFitnessPal-mcp
Readiness: IN_PROGRESS

## Goal and status

Provide locally cached nutrition to Home Assistant. Live dashboard acceptance PASSED; reboot reliability, reconnect testing and release handoff remain.

## Delivery evidence

- Implemented: feature/browser-onboarding commits e4f6fc4 and 3be947b were fast-forward merged into local main on 2026-09-20; package metadata 0.3.0.
- Tested: 2026-09-20: 159 Python tests and six card tests passed in their latest respective runs. Earlier WSL/Compose tests verified viewer WebSocket access, isolation and IP-address TLS.
- Released: local main contains the work; it has not been pushed, tagged or published.
- Deployed: Ubuntu /opt/myfitnesspal-mcp; app image ID prefix 7e2f45179d60 deployed 2026-09-20 20:29 EDT. Browser mobile-login image prefix fbf59ec30084cd4fd; Caddy 2.10.2-alpine. HA card v0.1.1 deployed separately.
- Live-verified: 2026-09-20 20:30 EDT: backend contains all four numeric nutrition totals/targets. User screenshot confirms HA Nutrition Test PASS, units, diary date and fresh source timestamp, with HA read at 20:30:53 EDT. Dashboard connection acceptance is complete.

## Completed

- Scripted LAN deployment, HTTPS/admin setup, HA source-IP restriction, certificate trust, migration and backup utilities. Legacy container retained stopped; rollback images retained.
- Manual cookie import/sync, mobile server-browser viewer, sanitized activity panel, explicit queue rejection and forced manual refresh. Missing nutrition reports incomplete; successful import closes the old browser lease.
- Principal-bound diary username correction without renaming credentials/archive identity. Account mismatch fails closed.
- Codex Nutrition Test reads through HA MCP; all eight previous views preserved. Header/encoding defects fixed; live PASS user-confirmed.
- Gateway recovered from boot/network race; persistent recovery service/timer and installer implemented, but installation/reboot verification not confirmed.

## HA / API / data contract

- MCP: http://192.168.50.220:8484/mcp; allowed HA source IP 192.168.50.10. Setup: https://192.168.50.220:8443/. Not public internet endpoints.
- Web tools: fitness_get_day(day?: YYYY-MM-DD), fitness_connection_status(), fitness_sync_today().
- Day response: cache status, data.nutrition.nutrients/goals, diary date, retrieved_at, stale and refresh_queued. Queue acceptance is not completion; missing data is not zero.
- Verified fields: calorie totals/targets (kcal), protein/carbohydrate/fat totals/targets (g), HA-local date and source freshness. Units are declared archive mappings. PASS requires current-day/non-stale data with source timestamp within 15 minutes.
- No native nutrition sensors required or created. dashboard-codex/nutrition-test uses HA's authenticated per-API MCP route and requires an administrator. Non-admin consumption needs separate design/authorization.

## Remaining coding, testing and deployment

1. Confirm/install scripted boot recovery, then test an agreed reboot: setup access, HA reads, credentials, archive and diary routing must survive.
2. Test token renewal/expiry and reconnect end to end, including live import/lease cleanup. Verify scheduled refresh across a day boundary and recovery from stale/error states.
3. Make diary-username correction available through setup rather than requiring the repair script; clarify administrator versus MFP credentials.
4. Record which committed revision is next deployed, verify backup/restore procedure and establish a traceable release/tag if publishing is later desired.

## Deferred / optional

- Browser-only Cloudflare login if manual token import is acceptable for v0.1; otherwise it remains a scope blocker.
- Polished production nutrition view, non-admin access, historical charts, MQTT/native sensors and broader web-mode tools. Not required for the completed MCP read contract.

## Actions needed from user

- Confirm whether boot-recovery installation was completed; otherwise run scripts/install-boot-recovery.ps1 when ready for its sudo prompt, and agree a reboot test window.
- No new token or dashboard setup currently needed. Keep credentials out of chat.

## Maintenance

Canonical status; supersedes resolved diagnostic blockers. Update after meaningful progress with implemented/released/deployed/live-verified distinctions. Do not mark READY from tests alone or store secrets/personal nutrition values.
