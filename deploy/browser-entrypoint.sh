#!/usr/bin/env bash
set -euo pipefail
umask 077
cleanup() { kill $(jobs -pr) 2>/dev/null || true; }
trap cleanup EXIT INT TERM
# Portrait display matches the mobile-emulated Chromium viewport.
Xvfb :99 -screen 0 480x900x24 -nolisten tcp &
for attempt in {1..50}; do
  if xdpyinfo -display :99 >/dev/null 2>&1; then break; fi
  sleep 0.1
done
xdpyinfo -display :99 >/dev/null
# Raw VNC is loopback-only; the gateway authorizes every noVNC request.
x11vnc -display :99 -localhost -forever -shared -nopw -rfbport 5900 -quiet &
websockify --web /usr/share/novnc 6080 localhost:5900 &
mfp-mcp --host 0.0.0.0 --port 8090 browser &
wait -n
exit 1
