[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*$')]
    [string]$SshTarget = 'zensqrl@192.168.50.220',
    [string]$IdentityFile = (Join-Path $env:USERPROFILE '.ssh/id_ed25519_myfitnesspal_mcp')
)
$ErrorActionPreference = 'Stop'
$IdentityFile = [IO.Path]::GetFullPath($IdentityFile)
$keyDirectory = Split-Path $IdentityFile -Parent
if (-not (Test-Path -LiteralPath $keyDirectory)) {
    New-Item -ItemType Directory -Path $keyDirectory | Out-Null
}
if (-not (Test-Path -LiteralPath $IdentityFile)) {
    Write-Host 'Creating a dedicated SSH key. For unattended access, leave its passphrase empty.'
    Write-Host 'The private key stays on this Windows account; only the public key is uploaded.'
    & ssh-keygen -t ed25519 -f $IdentityFile -C 'myfitnesspal-mcp-deployment'
    if ($LASTEXITCODE -ne 0) { throw 'Key generation failed.' }
}
if (-not (Test-Path -LiteralPath ($IdentityFile + '.pub'))) {
    throw 'Public key file is missing. Choose another IdentityFile; existing keys are never overwritten.'
}
# Apply an owner-only ACL to the private key, including on newly created files.
$principal = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls $IdentityFile /inheritance:r /grant:r "${principal}:F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not secure the private key.' }
$publicKey = (Get-Content -LiteralPath ($IdentityFile + '.pub') -Raw).Trim()
if ($publicKey -notmatch '^ssh-ed25519 [A-Za-z0-9+/]+={0,3}( .*)?$') {
    throw 'Expected an Ed25519 public key.'
}
# Strip the optional comment before composing a shell command. The remaining
# algorithm/base64 pair contains no shell metacharacters or private material.
$keyFields = $publicKey -split '\s+', 3
$publicKey = $keyFields[0] + ' ' + $keyFields[1]
$remote = "set -eu; umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys; grep -qF '$publicKey' ~/.ssh/authorized_keys || printf '\n%s\n' '$publicKey' >> ~/.ssh/authorized_keys"
Write-Host "Enter your existing server password when SSH prompts for $SshTarget."
& ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive $SshTarget $remote
if ($LASTEXITCODE -ne 0) { throw 'Public-key installation failed.' }
& ssh -i $IdentityFile -o IdentitiesOnly=yes $SshTarget 'echo SSH-key-access-verified'
if ($LASTEXITCODE -ne 0) { throw 'Key authentication failed.' }
Write-Host "Key installed: $IdentityFile"
Write-Host 'No sudo policy or server password was changed.'
Write-Host 'If you chose a passphrase, load the key into your SSH agent before unattended use.'
