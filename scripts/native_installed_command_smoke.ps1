$ErrorActionPreference = 'Stop'
if (-not $env:XAA_NATIVE_COMMAND_DISTRO -or -not $env:XAA_NATIVE_COMMAND_MODEL) {
    throw 'Set XAA_NATIVE_COMMAND_DISTRO and XAA_NATIVE_COMMAND_MODEL to already installed dependencies.'
}
$xaaWorkspace = (Get-Location).Path
$xaaAcceptanceParent = [IO.Path]::GetFullPath((Join-Path $xaaWorkspace '.acceptance')).TrimEnd('\') + '\'
$xaaRoot = [IO.Path]::GetFullPath((Join-Path $xaaAcceptanceParent ('installed-command-' + [guid]::NewGuid().ToString('N'))))
$xaaInstall = [IO.Path]::GetFullPath((Join-Path $xaaRoot 'install'))
if (-not $xaaRoot.StartsWith($xaaAcceptanceParent, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $xaaRoot)) {
    throw 'Acceptance output must be a new directory under .acceptance.'
}
New-Item -ItemType Directory -Path $xaaRoot | Out-Null
$xaaManifest = Get-Content -LiteralPath 'build/installer/manifest.json' -Raw | ConvertFrom-Json
$xaaInstallerName = $xaaManifest.artifacts.installer.filename
if ([IO.Path]::GetFileName($xaaInstallerName) -ne $xaaInstallerName) { throw 'Installer manifest filename escaped its staging directory.' }
$xaaInstaller = (Resolve-Path -LiteralPath (Join-Path 'build/installer' $xaaInstallerName)).Path
if ((Get-FileHash -LiteralPath $xaaInstaller -Algorithm SHA256).Hash -ne $xaaManifest.artifacts.installer.sha256) { throw 'Installer checksum mismatch.' }
$xaaReport = @{ passed = $false; installer_sha256 = $xaaManifest.artifacts.installer.sha256; install_path = $xaaInstall }
$xaaUninstaller = Join-Path $xaaInstall 'uninstall.exe'
try {
    $xaaSetup = Start-Process -FilePath $xaaInstaller -ArgumentList @('/S', "/D=$xaaInstall") -WindowStyle Hidden -Wait -PassThru
    if ($xaaSetup.ExitCode -ne 0) { throw "Installer exit code $($xaaSetup.ExitCode)" }
    $xaaDesktop = Join-Path $xaaInstall 'xaa-desktop.exe'
    $xaaEngine = Join-Path $xaaInstall 'xaa-engine.exe'
    if (-not (Test-Path -LiteralPath $xaaDesktop) -or -not (Test-Path -LiteralPath $xaaEngine)) { throw 'Installed desktop or engine is missing.' }
    if ((Get-FileHash -LiteralPath $xaaDesktop -Algorithm SHA256).Hash -ne $xaaManifest.artifacts.desktop.sha256 -or (Get-FileHash -LiteralPath $xaaEngine -Algorithm SHA256).Hash -ne $xaaManifest.artifacts.engine.sha256) { throw 'Installed binary checksum mismatch.' }
    $xaaReport.installed_binary_hashes_match = $true
    $env:XAA_NATIVE_INSTALLED_DESKTOP = $xaaDesktop
    $env:XAA_NATIVE_ACCEPTANCE_ROOT = Join-Path $xaaRoot 'command-acceptance'
    $env:XAA_NATIVE_PROJECT_FIX = '1'
    node scripts/native_command_smoke.mjs
    if ($LASTEXITCODE -ne 0) { throw 'Installed command/project-fix acceptance failed.' }
    $xaaReport.command_acceptance = Get-Content -LiteralPath (Join-Path $env:XAA_NATIVE_ACCEPTANCE_ROOT 'report.json') -Raw | ConvertFrom-Json
    if (-not $xaaReport.command_acceptance.passed -or -not $xaaReport.command_acceptance.project_fix_verified -or -not $xaaReport.command_acceptance.graceful_shutdown) { throw 'Installed acceptance report is incomplete.' }
    $xaaReport.installed_application_command_verified = $true
} finally {
    Remove-Item Env:XAA_NATIVE_INSTALLED_DESKTOP -ErrorAction SilentlyContinue
    Remove-Item Env:XAA_NATIVE_ACCEPTANCE_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:XAA_NATIVE_PROJECT_FIX -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $xaaUninstaller) {
        $xaaUninstall = Start-Process -FilePath $xaaUninstaller -ArgumentList '/S' -WindowStyle Hidden -Wait -PassThru
        $xaaReport.uninstaller_exit_code = $xaaUninstall.ExitCode
        $xaaReport.binaries_removed = -not (Test-Path -LiteralPath (Join-Path $xaaInstall 'xaa-desktop.exe')) -and -not (Test-Path -LiteralPath (Join-Path $xaaInstall 'xaa-engine.exe'))
    }
    $xaaReport.passed = $xaaReport.installed_application_command_verified -and $xaaReport.binaries_removed -and $xaaReport.uninstaller_exit_code -eq 0
    $xaaReport | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $xaaRoot 'report.json') -Encoding utf8
}
if (-not $xaaReport.passed) { throw 'Installed command or uninstallation verification failed.' }
Write-Output "Installed command acceptance passed: $xaaRoot"
