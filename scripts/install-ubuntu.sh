#!/usr/bin/env bash
# Run from a feature-branch checkout. Re-running preserves state and secrets.
set -euo pipefail
umask 077
ROOT=/opt/myfitnesspal-mcp
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ $EUID -eq 0 ]] || { echo 'Run this script with sudo.' >&2; exit 1; }
[[ -f /etc/os-release ]] || exit 1
. /etc/os-release
[[ $ID == ubuntu ]] || { echo 'This installer supports Ubuntu.' >&2; exit 1; }
if [[ -L $ROOT ]]; then echo 'Installation root must not be a symlink.' >&2; exit 1; fi
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' \
    "$(dpkg --print-architecture)" "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi
docker info >/dev/null || { echo 'Docker is installed but unavailable. Start Docker/Desktop and rerun.' >&2; exit 1; }
if ! docker buildx version >/dev/null 2>&1; then
  apt-get update
  if dpkg-query -W -f='${Status}' docker-ce 2>/dev/null | grep -q 'install ok installed'; then
    apt-get install -y docker-buildx-plugin
  else
    apt-get install -y docker-buildx
  fi
fi
apt-get update
apt-get install -y python3 python3-bcrypt openssl rsync
install -d -m 0755 "$ROOT" "$ROOT/app" "$ROOT/config"
install -d -m 0700 "$ROOT/secrets" "$ROOT/backups"
if [[ $SOURCE != "$ROOT/app" ]]; then
  if [[ -d "$ROOT/app/.git" ]] && [[ -n $(git -C "$ROOT/app" status --porcelain) ]]; then
    echo 'Server checkout has local edits; refusing to overwrite it.' >&2; exit 1
  fi
  # No deletion; exclude all local data and credentials. Deployment is a source
  # snapshot, so uncommitted feature-branch changes can also be tested.
  rsync -a --exclude='.git' --exclude='.venv' --exclude='state' --exclude='secrets' \
    --exclude='backups' --exclude='.env*' --exclude='*.db*' --exclude='cookies*' \
    --exclude='__pycache__' --exclude='.pytest_cache' --exclude='*.tar.gz' \
    "$SOURCE/" "$ROOT/app/"
fi
for directory in config data browser; do
  install -d -o 10001 -g 10001 -m 0700 "$ROOT/state/$directory"
done
install -d -m 0700 "$ROOT/state/gateway" "$ROOT/state/gateway-config"
if [[ ! -f "$ROOT/config/deployment.env" ]]; then
  read -rp 'Server LAN IPv4 address: ' bind_ip
  read -rp "Setup hostname/IP [$bind_ip]: " hostname
  hostname=${hostname:-$bind_ip}
  read -rp 'Home Assistant IPv4 address [192.168.50.10]: ' ha_ip
  ha_ip=${ha_ip:-192.168.50.10}
  read -rp 'Administrator network CIDR (example 192.168.50.0/24): ' admin_cidr
  read -rp 'Timezone [America/New_York]: ' timezone
  timezone=${timezone:-America/New_York}
  python3 - "$bind_ip" "$hostname" "$ha_ip" "$admin_cidr" "$timezone" <<'PY'
import ipaddress, re, sys
from zoneinfo import ZoneInfo
ipaddress.IPv4Address(sys.argv[1]); ipaddress.IPv4Address(sys.argv[3])
ipaddress.IPv4Network(sys.argv[4], strict=False)
assert re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}', sys.argv[2]), 'Invalid host'
assert re.fullmatch(r'[a-zA-Z0-9_+/-]+', sys.argv[5]), 'Invalid timezone'
ZoneInfo(sys.argv[5])
PY
  printf 'MFP_ROOT=%s\nMFP_BIND_IP=%s\nMFP_HOST=%s\nMFP_HA_IP=%s\nMFP_ADMIN_CIDR=%s\nMFP_TIMEZONE=%s\n' \
    "$ROOT" "$bind_ip" "$hostname" "$ha_ip" "$admin_cidr" "$timezone" > "$ROOT/config/deployment.env"
fi
for name in browser_token gateway_token; do
  if [[ ! -f "$ROOT/secrets/$name" ]]; then openssl rand -hex 32 > "$ROOT/secrets/$name"; fi
  chown 10001:10001 "$ROOT/secrets/$name"
  chmod 0600 "$ROOT/secrets/$name"
done
if [[ ! -f "$ROOT/secrets/gateway.env" ]]; then
  python3 - "$ROOT/secrets" <<'PY'
import bcrypt, getpass, pathlib, sys
root = pathlib.Path(sys.argv[1])
password = getpass.getpass('Setup administrator password (12–72 UTF-8 bytes): ')
if not 12 <= len(password.encode()) <= 72:
    raise SystemExit('Password must be 12–72 UTF-8 bytes.')
if password != getpass.getpass('Repeat password: '):
    raise SystemExit('Passwords do not match.')
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
token = (root / 'gateway_token').read_text().strip()
(root / 'gateway.env').write_text(f"MFP_ADMIN_HASH='{hashed}'\nMFP_GATEWAY_TOKEN={token}\n")
PY
fi
chmod 0600 "$ROOT/secrets/gateway.env" "$ROOT/config/deployment.env"
cd "$ROOT/app"
# Build before interrupting any existing service. The migration helper checks
# exact legacy mounts, takes a stopped-state backup, and handles rollback.
docker compose --env-file "$ROOT/config/deployment.env" -f deploy/compose.yaml build
. scripts/migrate-deployment.sh
prepare_legacy_migration
docker compose --env-file "$ROOT/config/deployment.env" -f deploy/compose.yaml up -d --no-build --wait --wait-timeout 180
ca_file="$ROOT/state/gateway/caddy/pki/authorities/local/root.crt"
for attempt in {1..40}; do
  [[ -f $ca_file ]] && break
  sleep 0.25
done
install -m 0644 "$ca_file" "$ROOT/config/root.crt"
set -a
. "$ROOT/config/deployment.env"
set +a
echo "Setup: https://$MFP_HOST:8443 (username admin)"
echo "Home Assistant MCP URL: http://$MFP_BIND_IP:8484/mcp"
echo "Allowed Home Assistant IP: $MFP_HA_IP"
echo "Public CA certificate: $ROOT/config/root.crt"
sha256sum "$ROOT/config/root.crt"
echo 'Copy/trust only root.crt; never distribute root.key or the secrets directory.'
bash scripts/verify-deployment.sh
finish_legacy_migration
bash scripts/install-boot-recovery.sh
