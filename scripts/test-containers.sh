#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
engine=${MFP_DOCKER_BIN:-docker}
"$engine" build -f deploy/Dockerfile --target app -t mfp-onboarding-app:test .
"$engine" build -f deploy/Dockerfile --target browser -t mfp-onboarding-browser:test .
"$engine" run --rm --init --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --security-opt "seccomp=$PWD/deploy/chromium-seccomp.json" \
  --shm-size 1g --tmpfs /tmp --tmpfs /home/mfp:uid=10001,gid=10001,mode=700 \
  --mount "type=bind,source=$PWD/scripts/browser-smoke.py,target=/smoke.py,readonly" \
  --entrypoint /usr/bin/xvfb-run mfp-onboarding-browser:test -a python /smoke.py
python3 scripts/compose-smoke.py
