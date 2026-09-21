# Personal Dashboard v0.1 — MyFitnessPal MCP handoff

Last updated: 2026-09-21 (America/New_York)
Repository: https://github.com/Zensqrl/myfitnesspal-mcp
Readiness: IN_PROGRESS

## Goal and status

Provide locally cached nutrition to Home Assistant. Live dashboard acceptance PASSED. Setup archive utilities are implemented and deployed; their controls and archive read are live-verified, while historical execution awaits a renewed MyFitnessPal session. Reboot reliability, session reliability and setup polish remain on the roadmap.

## Delivery evidence

- Implemented: setup archive utilities commit a58b8a3 on feature/setup-archive-utilities (2026-09-21). Backfill-through-today, inclusive range sync, skip/force, progress/cancellation, protected persistence and rich/raw day viewing are present. Package metadata remains 0.3.0.
- Tested: 2026-09-21: 166 Python tests and six dashboard-card tests passed; setup JavaScript syntax and git whitespace checks passed.
- Released: main was pushed to GitHub on 2026-09-20. No release tag or published package was created.
- Deployed: Ubuntu /opt/myfitnesspal-mcp; app image sha256:9b953e894d65 deployed healthy on 2026-09-21 06:14 EDT from the feature branch. Browser mobile-login image prefix fbf59ec30084cd4fd and Caddy 2.10.2-alpine were retained. HA card v0.1.1 is deployed separately.
- Live-verified: 2026-09-21: all three utility forms were served and the archive API returned the cached day contract (`data`, `day`, `found`, `sync`) without exposing account metadata. Historical execution correctly returned HTTP 409 because the saved upstream session is currently marked reconnect-required; no forced upstream refresh was attempted. Earlier 2026-09-20 user evidence confirms the HA Nutrition Test PASS and complete/fresh nutrition contract.

## Completed

- Scripted LAN deployment, HTTPS/admin setup, HA source-IP restriction, certificate trust, migration and backup utilities. Legacy container retained stopped; rollback images retained.
- Manual cookie import/sync, mobile server-browser viewer, sanitized activity panel, explicit queue rejection and forced manual refresh. Missing nutrition reports incomplete; successful import closes the old browser lease.
- Setup archive utilities: historical backfill through today, inclusive date-range sync, complete-cache skipping, explicit force refresh, progress and cooperative cancellation, plus rich and raw cached-day views. Incomplete upstream nutrition cannot replace a valid cached day.
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

1. After reconnecting MyFitnessPal, live-test a small cached range (skip), a small forced range, backfill progress/cancellation, and both viewer tabs; confirm the deployed UI from desktop and mobile.
2. Confirm/install scripted boot recovery, then test an agreed reboot: setup access, HA reads, credentials, archive and diary routing must survive.
3. Test token renewal/expiry and reconnect end to end, including live import/lease cleanup. Verify scheduled refresh across a day boundary and recovery from stale/error states.
4. Make diary-username correction available through setup rather than requiring the repair script; clarify administrator versus MyFitnessPal credentials and continue general setup polish.
5. Merge/push the feature after acceptance, verify backup/restore procedure and establish a traceable release/tag if publishing is later desired.

## Deferred / optional

- Browser-only Cloudflare login if manual token import is acceptable for v0.1; otherwise it remains a scope blocker.
- Polished production nutrition view, non-admin access, historical charts, MQTT/native sensors and broader web-mode tools. Not required for the completed MCP read contract.

## Actions needed from user

- Renew the MyFitnessPal session through the setup page before live historical-sync acceptance; do not send the token in chat.
- Confirm whether boot-recovery installation was completed; otherwise run scripts/install-boot-recovery.ps1 when ready for its sudo prompt, and agree a reboot test window.

## Maintenance

Canonical status; supersedes resolved diagnostic blockers. Update after meaningful progress with implemented/released/deployed/live-verified distinctions. Do not mark READY from tests alone or store secrets/personal nutrition values.
