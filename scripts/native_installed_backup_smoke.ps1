$ErrorActionPreference = 'Stop'
$xaaAcceptanceParent = [IO.Path]::GetFullPath((Join-Path (Get-Location).Path '.acceptance')).TrimEnd('\') + '\'
$xaaRoot = [IO.Path]::GetFullPath((Join-Path $xaaAcceptanceParent ('native-installed-backup-' + [guid]::NewGuid().ToString('N'))))
if (-not $xaaRoot.StartsWith($xaaAcceptanceParent, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $xaaRoot)) {
    throw 'Local installed acceptance must use a new directory under .acceptance.'
}
$xaaInstall = Join-Path $xaaRoot 'install'
$xaaLocalAppData = Join-Path $xaaRoot 'local-app-data'
$xaaFixture = Join-Path $xaaRoot 'fixture.json'
$xaaManifest = Get-Content -LiteralPath 'build/installer/manifest.json' -Raw | ConvertFrom-Json
$xaaInstallerName = $xaaManifest.artifacts.installer.filename
if ([IO.Path]::GetFileName($xaaInstallerName) -ne $xaaInstallerName) { throw 'Installer manifest filename escaped its staging directory.' }
$xaaInstaller = Join-Path 'build/installer' $xaaInstallerName
if (-not (Test-Path -LiteralPath $xaaInstaller) -or (Get-FileHash -LiteralPath $xaaInstaller -Algorithm SHA256).Hash -ne $xaaManifest.artifacts.installer.sha256) { throw 'Current installer checksum mismatch.' }
$xaaPriorLocalAppData = $env:LOCALAPPDATA
$xaaReport = @{ passed = $false; installer_sha256 = $xaaManifest.artifacts.installer.sha256; install_path = $xaaInstall; data_profile = $xaaLocalAppData }
$xaaMarker = $null
$xaaMarkerHash = $null
New-Item -ItemType Directory -Path $xaaRoot | Out-Null
New-Item -ItemType Directory -Path $xaaLocalAppData | Out-Null
$env:LOCALAPPDATA = $xaaLocalAppData
try {
    $xaaSetup = Start-Process -FilePath (Resolve-Path -LiteralPath $xaaInstaller).Path -ArgumentList @('/S', "/D=$xaaInstall") -WindowStyle Hidden -Wait -PassThru
    if ($xaaSetup.ExitCode -ne 0) { throw "Installer exit code $($xaaSetup.ExitCode)" }
    $xaaDesktop = Join-Path $xaaInstall 'xaa-desktop.exe'
    $xaaEngine = Join-Path $xaaInstall 'xaa-engine.exe'
    if (-not (Test-Path -LiteralPath $xaaDesktop) -or -not (Test-Path -LiteralPath $xaaEngine)) { throw 'Installed desktop or frozen engine is missing.' }
    if ((Get-FileHash -LiteralPath $xaaDesktop -Algorithm SHA256).Hash -ne $xaaManifest.artifacts.desktop.sha256 -or (Get-FileHash -LiteralPath $xaaEngine -Algorithm SHA256).Hash -ne $xaaManifest.artifacts.engine.sha256) { throw 'Installed executable hash mismatch.' }
    $xaaDataDirectory = Join-Path $xaaLocalAppData 'XAA'
    New-Item -ItemType Directory -Path $xaaDataDirectory -Force | Out-Null
    $xaaMarker = Join-Path $xaaDataDirectory 'preservation-fixture.txt'
    [IO.File]::WriteAllText($xaaMarker, 'Installed backup preservation fixture')
    $xaaMarkerHash = (Get-FileHash -LiteralPath $xaaMarker -Algorithm SHA256).Hash
    uv run python scripts/installed_backup_fixture.py seed --directory $xaaDataDirectory --report $xaaFixture
    if ($LASTEXITCODE -ne 0) { throw 'Could not seed isolated installed package data.' }
    $env:XAA_NATIVE_ACCEPTANCE_ROOT = Join-Path $xaaRoot 'native'
    $env:XAA_NATIVE_BACKUP_FIXTURE = $xaaFixture
    node scripts/native_boot_smoke.mjs $xaaDesktop
    if ($LASTEXITCODE -ne 0) { throw 'Installed desktop backup/restore acceptance failed.' }
    $xaaNativeReport = Get-Content -LiteralPath (Join-Path $env:XAA_NATIVE_ACCEPTANCE_ROOT 'report.json') -Raw | ConvertFrom-Json
    if (-not $xaaNativeReport.installed_backup_restore_verified -or -not $xaaNativeReport.backup_acceptance.verification.passed) { throw 'Installed backup/restore evidence was incomplete.' }
    $xaaReport.native_acceptance = $xaaNativeReport
    $xaaReport.installed_backup_restore_verified = $true
} catch {
    $xaaReport.error = $_.Exception.Message
    throw
} finally {
    $xaaUninstaller = Join-Path $xaaInstall 'uninstall.exe'
    if (Test-Path -LiteralPath $xaaUninstaller) {
        $xaaUninstall = Start-Process -FilePath $xaaUninstaller -ArgumentList '/S' -WindowStyle Hidden -Wait -PassThru
        $xaaReport.uninstaller_exit_code = $xaaUninstall.ExitCode
        $xaaReport.binaries_removed = -not (Test-Path -LiteralPath (Join-Path $xaaInstall 'xaa-desktop.exe')) -and -not (Test-Path -LiteralPath (Join-Path $xaaInstall 'xaa-engine.exe'))
    }
    $xaaReport.data_preserved = $null -ne $xaaMarker -and $null -ne $xaaMarkerHash -and (Test-Path -LiteralPath $xaaMarker) -and (Get-FileHash -LiteralPath $xaaMarker -Algorithm SHA256).Hash -eq $xaaMarkerHash
    $xaaReport.passed = [bool]$xaaReport.installed_backup_restore_verified -and [bool]$xaaReport.binaries_removed -and [bool]$xaaReport.data_preserved -and $xaaReport.uninstaller_exit_code -eq 0
    $xaaReport | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $xaaRoot 'report.json') -Encoding utf8
    Remove-Item Env:XAA_NATIVE_ACCEPTANCE_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:XAA_NATIVE_BACKUP_FIXTURE -ErrorAction SilentlyContinue
    $env:LOCALAPPDATA = $xaaPriorLocalAppData
}
if (-not $xaaReport.passed) { throw "Local installed backup acceptance failed; see $xaaRoot" }
Write-Output "Installed backup/restore acceptance passed: $xaaRoot"
