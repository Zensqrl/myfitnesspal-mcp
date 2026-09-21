#!/usr/bin/env bash
# Stop credential/browser writers for a consistent private backup; always restart.
set -euo pipefail
umask 077
ROOT=/opt/myfitnesspal-mcp
[[ $EUID -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
cd "$ROOT/app"
compose=(docker compose --env-file "$ROOT/config/deployment.env" -f deploy/compose.yaml)
[[ $("${compose[@]}" ps --status running -q app | wc -l) -eq 1 ]] || { echo 'App must be running before backup.' >&2; exit 1; }
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$ROOT/backups/$stamp"
mkdir -m 0700 "$backup"
trap '"${compose[@]}" start app browser >/dev/null' EXIT
"${compose[@]}" stop app browser
python3 - "$ROOT/state/data" "$backup" <<'PY'
import pathlib, shutil, sqlite3, sys
source, dest = map(pathlib.Path, sys.argv[1:])
for db in source.rglob('*.db'):
    target = dest / 'sqlite' / db.relative_to(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as old, sqlite3.connect(target) as new:
        old.backup(new)
for identity in source.rglob('principal.sha256'):
    target = dest / 'sqlite' / identity.relative_to(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(identity, target)
PY
tar -czf "$backup/private-state.tar.gz" -C "$ROOT" config secrets state/config state/browser state/gateway state/gateway-config
chmod -R go-rwx "$backup"
echo "Backup saved to $backup. It contains credentials and private CA keys; store it securely."
