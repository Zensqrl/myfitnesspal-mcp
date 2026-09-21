#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run this installer with sudo.' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install -d -m 0755 /opt/myfitnesspal-mcp/config
install -o root -g root -m 0644 "$source_dir/recover-gateway.py" /opt/myfitnesspal-mcp/config/recover-gateway.py
install -o root -g root -m 0644 "$source_dir/../deploy/myfitnesspal-mcp-recovery.service" /etc/systemd/system/myfitnesspal-mcp-recovery.service
install -o root -g root -m 0644 "$source_dir/../deploy/myfitnesspal-mcp-recovery.timer" /etc/systemd/system/myfitnesspal-mcp-recovery.timer
systemctl daemon-reload
systemctl enable --now myfitnesspal-mcp-recovery.timer
systemctl start myfitnesspal-mcp-recovery.service
systemctl is-enabled myfitnesspal-mcp-recovery.timer
systemctl is-active myfitnesspal-mcp-recovery.timer
