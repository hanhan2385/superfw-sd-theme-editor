<#
  Sign an .exe with the local SuperFW self-signed certificate.

  Usage:
      powershell -ExecutionPolicy Bypass -File tools\sign_exe.ps1 -Path "dist\v0.31\SuperFW-SD-Theme-Editor-v0.31.exe"

  Run tools\install_codesign_cert.ps1 once first (creates + trusts the cert).

  IMPORTANT: sign AFTER packaging. The signature is appended to the PE file,
  so any rebuild invalidates it and the file hash changes - regenerate
  SHA256SUMS.txt afterwards.

  Keep this file ASCII-only (PowerShell 5.1 reads BOM-less UTF-8 as ANSI).
#>
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string]$TimestampServer = 'http://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Path)) {
    Write-Error "File not found: $Path"
    exit 1
}
$Path = (Resolve-Path $Path).Path

$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
    Where-Object { $_.Subject -like '*SuperFW Theme Editor*' } |
    Select-Object -First 1

if (-not $cert) {
    Write-Error "No SuperFW code-signing certificate found. Run tools\install_codesign_cert.ps1 first."
    exit 1
}

Write-Output ("Signing : " + $Path)
Write-Output ("Cert    : " + $cert.Subject + " (" + $cert.Thumbprint + ")")

# Timestamping is optional: it only keeps the signature valid after the
# certificate expires. On a restricted network the RFC3161 server may be
# unreachable, so fall back to a plain signature rather than failing.
$result = $null
if ($TimestampServer) {
    try {
        $result = Set-AuthenticodeSignature -FilePath $Path -Certificate $cert `
            -HashAlgorithm SHA256 -TimestampServer $TimestampServer
        if ($result.Status -ne 'Valid') {
            Write-Output ("Timestamped attempt returned " + $result.Status + " - retrying without timestamp.")
            $result = $null
        }
    } catch {
        Write-Output ("Timestamp server unreachable (" + $_.Exception.Message + ") - retrying without timestamp.")
        $result = $null
    }
}
if (-not $result) {
    $result = Set-AuthenticodeSignature -FilePath $Path -Certificate $cert -HashAlgorithm SHA256
    Write-Output "Signed without timestamp (signature expires with the certificate)."
}

Write-Output ("Status  : " + $result.Status)
if ($result.Status -ne 'Valid') {
    Write-Output ("Message : " + $result.StatusMessage)
    exit 1
}

$verify = Get-AuthenticodeSignature $Path
Write-Output ("Verify  : " + $verify.Status)
$item = Get-Item $Path
Write-Output ("Size    : " + $item.Length + " bytes")
Write-Output "OK - signature is valid, remember to regenerate SHA256SUMS.txt."
