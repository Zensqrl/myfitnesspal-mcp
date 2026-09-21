[CmdletBinding()]
param([ValidatePattern('^[A-Za-z0-9_.-]+$')][string]$Distribution = 'Ubuntu')
$ErrorActionPreference = 'Stop'
$distributions = (& wsl --list --quiet) -replace "`0", ''
if ($LASTEXITCODE -ne 0) { throw 'Cannot access WSL. Run from a normal Windows PowerShell session.' }
if ($distributions.Trim() -notcontains $Distribution) {
    & wsl --install -d $Distribution --no-launch
    Write-Host 'Finish the Ubuntu first-run account setup (and reboot if Windows requests it), then rerun this script.'
    exit
}
$source = Split-Path $PSScriptRoot -Parent
$linuxSource = & wsl -d $Distribution -- wslpath -a $source
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the checkout in WSL.' }
# The same installer handles Docker Desktop integration or installs native Docker.
& wsl -d $Distribution -- sudo bash "$($linuxSource.Trim())/scripts/install-ubuntu.sh"
if ($LASTEXITCODE -ne 0) { throw 'WSL setup failed. See the preceding error.' }
