[CmdletBinding()]
param(
    [string]$SshTarget = 'zensqrl@192.168.50.220',
    [string]$IdentityFile = "$env:USERPROFILE\.ssh\id_ed25519_myfitnesspal_mcp"
)
$ErrorActionPreference = 'Stop'
$options = @('-i', $IdentityFile, '-o', 'IdentitiesOnly=yes')
$archive = Join-Path ([IO.Path]::GetTempPath()) ('mfp-boot-' + [guid]::NewGuid().ToString('N') + '.tar.gz')
$remoteName = [IO.Path]::GetFileName($archive)
try {
    & tar -czf $archive -C (Split-Path $PSScriptRoot -Parent) scripts/recover-gateway.py scripts/install-boot-recovery.sh deploy/myfitnesspal-mcp-recovery.service deploy/myfitnesspal-mcp-recovery.timer
    if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
    & scp @options $archive "${SshTarget}:/tmp/$remoteName"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed.' }
    & ssh @options -t $SshTarget "set -eu; stage=`$(mktemp -d /tmp/mfp-boot.XXXXXXXX); tar -xzf /tmp/$remoteName -C `"`$stage`"; sudo bash `"`$stage/scripts/install-boot-recovery.sh`""
    if ($LASTEXITCODE -ne 0) { throw 'Boot recovery installation failed.' }
} finally {
    if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive }
}
