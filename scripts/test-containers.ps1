[CmdletBinding()]
param([switch]$Wsl)
$ErrorActionPreference = 'Stop'
$source = Split-Path $PSScriptRoot -Parent
if ($Wsl) {
    $linuxSource = & wsl -d Ubuntu -- wslpath -a $source
    if ($LASTEXITCODE -ne 0) { throw 'Cannot access Ubuntu WSL.' }
    & wsl -d Ubuntu -u root -- bash "$($linuxSource.Trim())/scripts/test-containers.sh"
    if ($LASTEXITCODE -ne 0) { throw 'WSL container tests failed.' }
    return
}
Push-Location $source
try {
    & docker build -f deploy/Dockerfile --target app -t mfp-onboarding-app:test .
    if ($LASTEXITCODE -ne 0) { throw 'App build failed.' }
    & docker build -f deploy/Dockerfile --target browser -t mfp-onboarding-browser:test .
    if ($LASTEXITCODE -ne 0) { throw 'Browser build failed.' }
    & docker run --rm --init --read-only --cap-drop ALL --security-opt no-new-privileges:true --shm-size 1g `
        --security-opt "seccomp=$source/deploy/chromium-seccomp.json" `
        --tmpfs /tmp --tmpfs /home/mfp:uid=10001,gid=10001,mode=700 `
        --mount "type=bind,source=$source/scripts/browser-smoke.py,target=/smoke.py,readonly" `
        --entrypoint /usr/bin/xvfb-run mfp-onboarding-browser:test -a python /smoke.py
    if ($LASTEXITCODE -ne 0) { throw 'Real Chromium smoke test failed.' }
} finally { Pop-Location }
