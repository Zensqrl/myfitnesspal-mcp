# Nutrition acceptance view

Card version: 0.1.1 (unreleased). Source: `mfp-acceptance-card.mjs`.

2026-09-20: deployed the explicit JSON Accept header fix and HTTP-status diagnostics to the existing module resource. Deployment reads source as UTF-8 and escapes non-ASCII characters as JavaScript Unicode escapes, preventing encoding corruption in the inline resource. Six card tests and 154 Python tests pass. Reload the HA frontend to replace the already registered custom element; the heading should show v0.1.1. Live acceptance still requires a successful read.

Deployed 2026-09-20 as an inline HA module resource and a new **Codex → Nutrition Test** view at `/dashboard-codex/nutrition-test`. All eight pre-existing views were preserved exactly. Browser rendering and real HA-routed data still require live verification.

The card uses the signed-in HA administrator's existing session to call HA's `/api/mcp/<api_id>` route. That route invokes the configured MyFitnessPal MCP integration; it does not contact an AI provider. No additional credentials, sensors, Ubuntu services, or public endpoints are created. HA 2026.9.2's MCP Server integration is required for this route. Non-administrators cannot use this acceptance test.

Configuration: `type: custom:mfp-acceptance-card`, `api_id: mcp-<MyFitnessPal integration entry ID>`. The live view already contains the correct ID. The registered module resource ID is `839537059cab4ae1b02b5ff8f4b625b9`; update that resource when changing the card, rather than adding duplicates. Source is retained here on `feature/browser-onboarding`.

## Acceptance

Open the view as an administrator. It reads `tools/list`, then `fitness_get_day` for today's date in HA's timezone. Reads repeat once per minute while visible; stale reads may queue the server's normal background refresh.

- PASS: numeric calorie/protein/carbohydrate/fat totals and targets, current HA-local diary date, cached/non-stale response, and source timestamp within 15 minutes.
- Units: kcal for calories and g for macros, explicitly mapped from the archive contract (not dynamically supplied unit labels).
- Missing, stale, malformed, wrong-day or future-dated data cannot pass. Missing values remain missing, never zero. Negative remaining values mean over target.
- Confirm the actual displayed values and source timestamp before closing the handoff item. Test results alone are insufficient.

Only the requested summary fields are displayed; no food names, notes, cookies or tokens are persisted by this card. Run offline card tests with `node --test dashboard/mfp-acceptance-card.test.mjs`.

Rollback: remove only the `nutrition-test` view and its dedicated resource above through HA dashboard configuration. No existing view or backend data needs changing.
