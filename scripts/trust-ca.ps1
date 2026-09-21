[CmdletBinding()]
param([Parameter(Mandatory)][string]$CertificatePath)
$ErrorActionPreference = 'Stop'
$resolved = (Resolve-Path -LiteralPath $CertificatePath).Path
$certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new($resolved)
if ($certificate.HasPrivateKey) { throw 'Use the public root.crt only.' }
Write-Host "Certificate: $($certificate.Subject)"
Write-Host "SHA-256 file fingerprint: $((Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash)"
if ((Read-Host 'Verify the fingerprint against the server copy. Type TRUST to trust this CA for your Windows user') -cne 'TRUST') { exit }
Import-Certificate -FilePath $resolved -CertStoreLocation Cert:\CurrentUser\Root | Out-Null
Write-Host 'CA trusted for the current Windows user. Firefox may require its own certificate import.'
