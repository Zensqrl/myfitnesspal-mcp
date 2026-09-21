#!/usr/bin/env bash
# Optional native Ubuntu/WSL test engine; does not alter Docker Desktop.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
. /etc/os-release
[[ $ID == ubuntu ]] || { echo 'Ubuntu required.' >&2; exit 1; }
if [[ ! -x /usr/bin/docker ]]; then
  apt-get update
  apt-get install -y docker.io docker-compose-v2 docker-buildx
fi
if ! /usr/bin/docker buildx version >/dev/null 2>&1; then
  apt-get update
  apt-get install -y docker-buildx
fi
if [[ $(cat /proc/1/comm) == systemd ]]; then
  systemctl start docker
else
  service docker start
fi
/usr/bin/docker info --format '{{.ServerVersion}}'
