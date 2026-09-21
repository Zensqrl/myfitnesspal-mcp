[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*$')][string]$SshTarget,
    [string]$Source = (Split-Path $PSScriptRoot -Parent),
    [string]$IdentityFile
)
$ErrorActionPreference = 'Stop'
$sshOptions = @()
if ($IdentityFile) { $sshOptions = @('-i', $IdentityFile, '-o', 'IdentitiesOnly=yes') }
$branch = & git -C $Source branch --show-current
if ($LASTEXITCODE -ne 0 -or $branch -ne 'feature/browser-onboarding') {
    throw 'Run deployment from feature/browser-onboarding.'
}
$archive = Join-Path ([IO.Path]::GetTempPath()) ('mfp-source-' + [guid]::NewGuid().ToString('N') + '.tar.gz')
try {
    # Explicit source allowlist: credentials, databases and browser profiles are never uploaded.
    & tar --exclude=__pycache__ --exclude='*.pyc' -czf $archive -C $Source pyproject.toml README.md src deploy scripts
    if ($LASTEXITCODE -ne 0) { throw 'Source packaging failed.' }
    $remoteName = 'mfp-source-' + [guid]::NewGuid().ToString('N') + '.tar.gz'
    & scp @sshOptions $archive "${SshTarget}:/tmp/$remoteName"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed.' }
    $remote = "set -eu; stage=`$(mktemp -d /tmp/mfp-stage.XXXXXXXX); tar -xzf /tmp/$remoteName -C `"`$stage`"; sudo bash `"`$stage/scripts/install-ubuntu.sh`""
    & ssh @sshOptions -t $SshTarget $remote
    if ($LASTEXITCODE -ne 0) { throw 'Deployment failed; inspect the preceding error and rerun.' }
} finally {
    if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive }
}
