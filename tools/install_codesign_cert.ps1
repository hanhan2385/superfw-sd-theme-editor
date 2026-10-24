<#
  Create a self-signed code-signing certificate and trust it for the current user.

  WHY: an unsigned .exe triggers the Windows "Open File - Security Warning"
  ("The publisher could not be verified"). Signing with a certificate that is
  trusted on this machine removes that prompt.

  SCOPE: everything here is per-user (Cert:\CurrentUser\*), no admin needed and
  no machine-wide trust is changed. To undo:
      Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
        Where-Object { $_.Subject -like "*SuperFW Theme Editor*" } |
        Remove-Item
      # and remove the same thumbprint from CurrentUser\Root / TrustedPublisher

  NOTE: keep this file ASCII-only. PowerShell 5.1 reads BOM-less UTF-8 as ANSI,
  so any non-ASCII character would arrive as mojibake.
#>
$ErrorActionPreference = 'Stop'

$subject = 'CN=SuperFW Theme Editor, O=SuperFW, C=CN'

$existing = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
    Where-Object { $_.Subject -like '*SuperFW Theme Editor*' } |
    Select-Object -First 1

if ($existing) {
    Write-Output "Certificate already exists, reusing it."
    $cert = $existing
} else {
    $cert = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject $subject `
        -KeyUsage DigitalSignature `
        -FriendlyName 'SuperFW Theme Editor (self-signed)' `
        -CertStoreLocation Cert:\CurrentUser\My `
        -NotAfter (Get-Date).AddYears(5) `
        -KeyExportPolicy Exportable
    Write-Output "Created a new code-signing certificate."
}

Write-Output ("  Subject    : " + $cert.Subject)
Write-Output ("  Thumbprint : " + $cert.Thumbprint)
Write-Output ("  NotAfter   : " + $cert.NotAfter)

foreach ($storeName in @('Root', 'TrustedPublisher')) {
    $store = New-Object System.Security.Cryptography.X509Certificates.X509Store($storeName, 'CurrentUser')
    $store.Open('ReadWrite')
    $already = $store.Certificates | Where-Object { $_.Thumbprint -eq $cert.Thumbprint }
    if ($already) {
        Write-Output ("  " + $storeName + ": already trusted")
    } else {
        $store.Add($cert)
        Write-Output ("  " + $storeName + ": added")
    }
    $store.Close()
}

$cer = Join-Path (Split-Path -Parent $PSScriptRoot) 'build\sfw-codesign.cer'
Export-Certificate -Cert $cert -FilePath $cer -Force | Out-Null
Write-Output ("Public certificate exported to: " + $cer)
Write-Output "Done. Now run:"
Write-Output '    powershell -NoProfile -ExecutionPolicy Bypass -File tools\sign_exe.ps1 -Path "dist\v0.31\SuperFW-SD-Theme-Editor-v0.31.exe"'
