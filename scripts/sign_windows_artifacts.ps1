param(
    [Parameter(Mandatory = $true)]
    [string[]]$Files
)

$ErrorActionPreference = 'Stop'
$env:PSModulePath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules'
Import-Module PKI -ErrorAction Stop
Import-Module Microsoft.PowerShell.Security -ErrorAction Stop

if ([string]::IsNullOrWhiteSpace($env:XAA_SIGNING_CERTIFICATE) -or -not (Test-Path -LiteralPath $env:XAA_SIGNING_CERTIFICATE -PathType Leaf)) {
    throw 'The signing certificate file is unavailable.'
}
if ($null -eq $env:XAA_SIGNING_PASSWORD) {
    throw 'XAA_SIGNING_PASSWORD must be set.'
}
foreach ($file in $Files) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        throw 'A release executable selected for signing is unavailable.'
    }
}

$signTool = if ($env:XAA_SIGNTOOL) {
    (Resolve-Path -LiteralPath $env:XAA_SIGNTOOL).Path
} else {
    (Get-Command signtool.exe -ErrorAction Stop).Source
}
$password = ConvertTo-SecureString $env:XAA_SIGNING_PASSWORD -AsPlainText -Force
$timestampUrl = if ([string]::IsNullOrWhiteSpace($env:XAA_SIGNING_TIMESTAMP_URL)) {
    'http://timestamp.digicert.com'
} else {
    $env:XAA_SIGNING_TIMESTAMP_URL
}
$pfx = Get-PfxData -FilePath (Resolve-Path -LiteralPath $env:XAA_SIGNING_CERTIFICATE).Path -Password $password
if (@($pfx.EndEntityCertificates).Count -ne 1) {
    throw 'The PFX must contain exactly one end-entity signing certificate.'
}
$certificate = $pfx.EndEntityCertificates[0]
$thumbprint = $certificate.Thumbprint
$storePath = "Cert:\CurrentUser\My\$thumbprint"
$preexistingThumbprints = @(Get-ChildItem -LiteralPath 'Cert:\CurrentUser\My' | ForEach-Object { $_.Thumbprint })
if (Test-Path -LiteralPath $storePath) {
    throw 'The signing certificate already exists in the current-user store; refusing to remove an existing certificate.'
}
try {
    $eku = @($certificate.EnhancedKeyUsageList | ForEach-Object { [string]$_.ObjectId })
    if ($eku -notcontains '1.3.6.1.5.5.7.3.3') {
        throw 'The certificate is not valid for code signing.'
    }
    $now = Get-Date
    if ($certificate.NotBefore -gt $now -or $certificate.NotAfter -le $now) {
        throw 'The code-signing certificate is outside its validity period.'
    }
    if (@($Files | Select-Object -Unique).Count -ne $Files.Count) {
        throw 'The release file list contains duplicates.'
    }
    $imported = Import-PfxCertificate `
        -FilePath (Resolve-Path -LiteralPath $env:XAA_SIGNING_CERTIFICATE).Path `
        -CertStoreLocation 'Cert:\CurrentUser\My' `
        -Password $password
    $signingCertificate = @($imported | Where-Object { $_.Thumbprint -eq $thumbprint }) | Select-Object -First 1
    if ($null -eq $signingCertificate -or -not $signingCertificate.HasPrivateKey) {
        throw 'The PFX did not import the selected signing certificate with its private key.'
    }
    foreach ($file in $Files) {
        & $signTool sign /sha1 $thumbprint /s My /fd SHA256 /tr $timestampUrl /td SHA256 /v $file
        if ($LASTEXITCODE -ne 0) {
            throw 'SignTool could not sign a release executable.'
        }
        & $signTool verify /pa /all /v $file
        if ($LASTEXITCODE -ne 0) {
            throw 'SignTool could not verify a signed release executable.'
        }
    }
} finally {
    Get-ChildItem -LiteralPath 'Cert:\CurrentUser\My' |
        Where-Object { $preexistingThumbprints -notcontains $_.Thumbprint } |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Remove-Variable password -ErrorAction SilentlyContinue
    Remove-Variable signingCertificate -ErrorAction SilentlyContinue
}
