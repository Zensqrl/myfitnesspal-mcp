[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*$')][string]$SshTarget,
    [string]$Destination = (Join-Path (Get-Location) 'mfp-root.crt'),
    [string]$IdentityFile
)
$ErrorActionPreference = 'Stop'
$sshOptions = @()
if ($IdentityFile) { $sshOptions = @('-i', $IdentityFile, '-o', 'IdentitiesOnly=yes') }
# The installer exports only the public CA certificate to this readable path.
& scp @sshOptions "${SshTarget}:/opt/myfitnesspal-mcp/config/root.crt" $Destination
if ($LASTEXITCODE -ne 0) { throw 'Cannot fetch the public CA certificate.' }
& ssh @sshOptions $SshTarget 'sha256sum /opt/myfitnesspal-mcp/config/root.crt'
if ($LASTEXITCODE -ne 0) { throw 'Cannot verify the server fingerprint.' }
Write-Host "Public CA saved to $Destination"
