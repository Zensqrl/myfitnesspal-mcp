#!/usr/bin/env bash
# Sourced by install-ubuntu.sh after images build successfully. These functions
# migrate only the verified legacy mfp-mcp deployment, never unrelated services.
prepare_legacy_migration() {
  LEGACY_STOPPED=0
  if ! docker container inspect mfp-mcp >/dev/null 2>&1; then return; fi
  legacy_config=$(docker inspect --format='{{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}' mfp-mcp)
  legacy_data=$(docker inspect --format='{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' mfp-mcp)
  if [[ $legacy_config != /opt/mcp-mfp/myfitnesspal-mcp/state/config ||
        $legacy_data != /opt/mcp-mfp/myfitnesspal-mcp/state/data ]]; then
    echo 'An existing mfp-mcp container has unexpected mounts; refusing automatic migration.' >&2
    exit 1
  fi
  if [[ $(docker inspect --format='{{.State.Running}}' mfp-mcp) != true ]]; then return; fi
  if [[ -n $(find "$ROOT/state/config" "$ROOT/state/data" -mindepth 1 -print -quit) ]]; then
    echo 'Both legacy and destination state exist. Refusing to overwrite either.' >&2
    exit 1
  fi
  echo 'Migrating the existing mfp-mcp deployment; original files and container are retained.'
  legacy_backup="$ROOT/backups/legacy-$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -m 0700 "$legacy_backup"
  docker stop --time 60 mfp-mcp >/dev/null
  LEGACY_STOPPED=1
  trap rollback_legacy EXIT
  tar -czf "$legacy_backup/state.tar.gz" -C /opt/mcp-mfp/myfitnesspal-mcp state
  rsync -a "$legacy_config/" "$ROOT/state/config/"
  rsync -a "$legacy_data/" "$ROOT/state/data/"
  # The copy is private and belongs to the new app UID. Old files are untouched.
  chown -R 10001:10001 "$ROOT/state/config" "$ROOT/state/data"
  chmod -R go-rwx "$ROOT/state/config" "$ROOT/state/data"
  echo "Private legacy state backup: $legacy_backup/state.tar.gz"
}

rollback_legacy() {
  result=$?
  if [[ ${LEGACY_STOPPED:-0} == 1 && $result != 0 ]]; then
    echo 'New deployment failed; stopping it and restarting the retained legacy container.' >&2
    docker compose --env-file "$ROOT/config/deployment.env" -f "$ROOT/app/deploy/compose.yaml" down || true
    docker start mfp-mcp >/dev/null || true
    echo 'Migrated copies remain in the destination for inspection; original state is unchanged.' >&2
  fi
  return "$result"
}

finish_legacy_migration() {
  if [[ ${LEGACY_STOPPED:-0} == 1 ]]; then
    # Keep the previous container recoverable without reclaiming port 8484 after
    # a host restart. Its original Compose file remains available for rollback.
    docker update --restart=no mfp-mcp >/dev/null
    echo 'Legacy container retained, stopped, with automatic restart disabled.'
  fi
  trap - EXIT
}
