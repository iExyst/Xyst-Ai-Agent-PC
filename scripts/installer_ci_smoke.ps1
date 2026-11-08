$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or -not $env:RUNNER_TEMP) {
    throw 'This install/uninstall test is restricted to a disposable GitHub Actions runner.'
}
$xaaRunnerRoot = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
$xaaLocalAppData = [IO.Path]::GetFullPath((Join-Path $env:RUNNER_TEMP 'xaa-isolated-localappdata'))
if (-not $xaaLocalAppData.StartsWith($xaaRunnerRoot, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $xaaLocalAppData)) {
    throw 'Isolated application data profile must be a new directory under RUNNER_TEMP.'
}
New-Item -ItemType Directory -Path $xaaLocalAppData | Out-Null
$xaaPriorLocalAppData = $env:LOCALAPPDATA
$env:LOCALAPPDATA = $xaaLocalAppData
$xaaInstallDir = [IO.Path]::GetFullPath((Join-Path $env:RUNNER_TEMP 'xaa-installer-test'))
if (-not $xaaInstallDir.StartsWith($xaaRunnerRoot, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $xaaInstallDir)) {
    throw 'Installer test destination must be a new directory under RUNNER_TEMP.'
}
$xaaManifest = Get-Content -LiteralPath 'build/installer/manifest.json' -Raw | ConvertFrom-Json
$xaaInstallerName = $xaaManifest.artifacts.installer.filename
if ([IO.Path]::GetFileName($xaaInstallerName) -ne $xaaInstallerName) { throw 'Installer manifest filename escaped its staging directory.' }
$xaaInstaller = (Resolve-Path -LiteralPath (Join-Path 'build/installer' $xaaInstallerName)).Path
if ((Get-FileHash -LiteralPath $xaaInstaller -Algorithm SHA256).Hash -ne $xaaManifest.artifacts.installer.sha256) { throw 'Installer checksum mismatch.' }
$xaaPriorInstaller = $null
$xaaPriorManifest = $null
$xaaPriorPath = Get-ChildItem -LiteralPath 'build/prior-installer' -Filter 'manifest.json' -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if ($xaaPriorPath) {
    $xaaPriorManifest = Get-Content -LiteralPath $xaaPriorPath.FullName -Raw | ConvertFrom-Json
    if ([version]$xaaPriorManifest.version -ge [version]$xaaManifest.version) { throw 'Upgrade fixture must be older than the current package.' }
    $xaaPriorName = $xaaPriorManifest.artifacts.installer.filename
    if ([IO.Path]::GetFileName($xaaPriorName) -ne $xaaPriorName) { throw 'Prior installer manifest filename escaped its directory.' }
    $xaaPriorInstaller = Join-Path $xaaPriorPath.DirectoryName $xaaPriorName
    if (-not (Test-Path -LiteralPath $xaaPriorInstaller) -or (Get-FileHash -LiteralPath $xaaPriorInstaller -Algorithm SHA256).Hash -ne $xaaPriorManifest.artifacts.installer.sha256) { throw 'Prior installer checksum mismatch.' }
}
$xaaDataDir = Join-Path $env:LOCALAPPDATA 'XAA'
$xaaMarker = Join-Path $xaaDataDir 'installer-preservation-fixture.txt'
if (Test-Path -LiteralPath $xaaMarker) { throw 'Data fixture already exists.' }
New-Item -ItemType Directory -Path $xaaDataDir -Force | Out-Null
[IO.File]::WriteAllText($xaaMarker, 'XAA installer preservation fixture')
$xaaMarkerHash = (Get-FileHash -LiteralPath $xaaMarker -Algorithm SHA256).Hash
$xaaAcceptanceRoot = [IO.Path]::GetFullPath((Join-Path (Get-Location).Path ('.acceptance\installed-backup-' + [guid]::NewGuid().ToString('N'))))
$xaaAcceptanceParent = [IO.Path]::GetFullPath((Join-Path (Get-Location).Path '.acceptance')).TrimEnd('\') + '\'
if (-not $xaaAcceptanceRoot.StartsWith($xaaAcceptanceParent, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $xaaAcceptanceRoot)) { throw 'Installed backup evidence path must be new and inside .acceptance.' }
New-Item -ItemType Directory -Path $xaaAcceptanceRoot | Out-Null
$xaaBackupFixture = Join-Path $xaaAcceptanceRoot 'fixture.json'
$xaaReport = @{ passed = $false; environment = 'disposable hosted Windows runner'; unsigned = $true }
try {
    if ($xaaPriorInstaller) {
        $xaaPriorSetup = Start-Process -FilePath $xaaPriorInstaller -ArgumentList @('/S', "/D=$xaaInstallDir") -WindowStyle Hidden -Wait -PassThru
        if ($xaaPriorSetup.ExitCode -ne 0) { throw "Prior installer exit code $($xaaPriorSetup.ExitCode)" }
        $xaaReport.prior_version = $xaaPriorManifest.version
        $xaaReport.prior_install_verified = (Test-Path -LiteralPath (Join-Path $xaaInstallDir 'xaa-engine.exe'))
        if (-not $xaaReport.prior_install_verified) { throw 'Prior installer did not install its engine.' }
        # Seed a real schema-18 profile while the predecessor package is installed.
        # The current package's backup/restore acceptance later proves its run, events,
        # artifact metadata and bytes survived the in-place upgrade.
        uv run python scripts/installed_backup_fixture.py seed --directory $xaaDataDir --report $xaaBackupFixture
        if ($LASTEXITCODE -ne 0) { throw 'Could not seed user data before the version upgrade.' }
        $xaaReport.upgrade_data_fixture_seeded_before_upgrade = $true
    }
    [IO.File]::WriteAllText($xaaMarker, 'XAA upgrade preservation fixture')
    $xaaMarkerHash = (Get-FileHash -LiteralPath $xaaMarker -Algorithm SHA256).Hash
    $xaaSetup = Start-Process -FilePath $xaaInstaller -ArgumentList @('/S', "/D=$xaaInstallDir") -WindowStyle Hidden -Wait -PassThru
    if ($xaaSetup.ExitCode -ne 0) { throw "Installer exit code $($xaaSetup.ExitCode)" }
    $xaaDesktop = Join-Path $xaaInstallDir 'xaa-desktop.exe'
    $xaaEngine = Join-Path $xaaInstallDir 'xaa-engine.exe'
    if (-not (Test-Path -LiteralPath $xaaDesktop) -or -not (Test-Path -LiteralPath $xaaEngine)) { throw 'Installed desktop or bundled engine missing.' }
    $xaaDesktopHash = (Get-FileHash -LiteralPath $xaaDesktop -Algorithm SHA256).Hash
    $xaaEngineHash = (Get-FileHash -LiteralPath $xaaEngine -Algorithm SHA256).Hash
    $xaaReport.installed_binary_hashes = @{
        desktop = @{ expected = $xaaManifest.artifacts.desktop.sha256; actual = $xaaDesktopHash }
        engine = @{ expected = $xaaManifest.artifacts.engine.sha256; actual = $xaaEngineHash }
    }
    if ($xaaDesktopHash -ne $xaaManifest.artifacts.desktop.sha256 -or $xaaEngineHash -ne $xaaManifest.artifacts.engine.sha256) {
        throw "Installed binary checksums do not match the manifest. Desktop expected $($xaaManifest.artifacts.desktop.sha256), actual $xaaDesktopHash; engine expected $($xaaManifest.artifacts.engine.sha256), actual $xaaEngineHash."
    }
    $xaaReport.installed_binary_hashes_match = $true
    foreach ($xaaNoticeName in @('THIRD_PARTY_NOTICES.md', 'dependency-inventory.md', 'release-notes-0.1.1.md')) {
        $xaaNotice = Join-Path $xaaInstallDir $xaaNoticeName
        $xaaNoticeMetadata = $xaaManifest.notices.$xaaNoticeName
        if (-not $xaaNoticeMetadata -or -not (Test-Path -LiteralPath $xaaNotice)) { throw "Installed release notice missing: $xaaNoticeName" }
        if ((Get-FileHash -LiteralPath $xaaNotice -Algorithm SHA256).Hash -ne $xaaNoticeMetadata.sha256) { throw "Installed release notice checksum mismatch: $xaaNoticeName" }
    }
    $xaaReport.installed_release_notices_verified = $true
    $xaaReport.upgrade_verified = [bool]$xaaPriorInstaller
    $xaaAcpHelp = & $xaaEngine --cli acp --help 2>&1
    if ($LASTEXITCODE -ne 0 -or -not (($xaaAcpHelp | Out-String) -match 'usage: xaa acp')) { throw 'Installed ACP CLI entry point failed.' }
    $xaaReport.installed_acp_cli_verified = $true
    if (-not (Test-Path -LiteralPath $xaaBackupFixture)) {
        uv run python scripts/installed_backup_fixture.py seed --directory $xaaDataDir --report $xaaBackupFixture
        if ($LASTEXITCODE -ne 0) { throw 'Could not seed disposable installed backup data.' }
    }
    # WebView2 150+ ignores WEBVIEW2_* environment overrides in elevated hosts.
    # Use the documented app-specific HKLM channel only on this disposable runner.
    # https://github.com/MicrosoftEdge/WebView2Feedback/issues/5645
    $xaaIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $xaaElevated = ([Security.Principal.WindowsPrincipal]::new($xaaIdentity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $xaaReport.runner_elevated = $xaaElevated
    $xaaDebugPolicy = 'HKLM:\Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments'
    $xaaDebugPolicyOwned = $false
    try {
        if ($xaaElevated) {
            $xaaExistingPolicy = Get-Item -LiteralPath $xaaDebugPolicy -ErrorAction SilentlyContinue
            if ($xaaExistingPolicy -and $xaaExistingPolicy.GetValueNames() -contains 'xaa-desktop.exe') { throw 'Refusing to replace existing app debugging configuration.' }
            New-Item -Path $xaaDebugPolicy -Force | Out-Null
            New-ItemProperty -LiteralPath $xaaDebugPolicy -Name 'xaa-desktop.exe' -Value '--remote-debugging-port=19222' -PropertyType String | Out-Null
            $xaaDebugPolicyOwned = $true
        }
        $env:XAA_NATIVE_ACCEPTANCE_ROOT = $xaaAcceptanceRoot
        $env:XAA_NATIVE_BACKUP_FIXTURE = $xaaBackupFixture
        & node scripts/native_boot_smoke.mjs $xaaDesktop
        $xaaBootExitCode = $LASTEXITCODE
    } finally {
        if ($xaaDebugPolicyOwned) {
            Remove-ItemProperty -LiteralPath $xaaDebugPolicy -Name 'xaa-desktop.exe'
            $xaaReport.debugging_configuration_removed = -not ((Get-Item -LiteralPath $xaaDebugPolicy).GetValueNames() -contains 'xaa-desktop.exe')
            if (-not $xaaReport.debugging_configuration_removed) { throw 'App debugging configuration cleanup failed.' }
        }
    }
    if ($xaaBootExitCode -ne 0) { throw 'Installed application boot/shutdown test failed.' }
    $xaaReport.native_acceptance = Get-Content -LiteralPath (Join-Path $xaaAcceptanceRoot 'report.json') -Raw | ConvertFrom-Json
    $xaaReport.installed_backup_restore_verified = [bool]$xaaReport.native_acceptance.installed_backup_restore_verified
    if (-not $xaaReport.installed_backup_restore_verified -or -not $xaaReport.native_acceptance.backup_acceptance.verification.passed) { throw 'Installed backup/restore evidence was incomplete.' }
    $xaaReport.upgrade_data_verified = [bool]$xaaPriorInstaller -and [bool]$xaaReport.upgrade_data_fixture_seeded_before_upgrade -and $xaaReport.installed_backup_restore_verified
    $xaaReport.installed_binaries_present = $true
    $xaaReport.installed_boot_verified = $true
} finally {
    $xaaInstallerLogDirectory = Join-Path (Get-Location).Path 'build/installer'
    $xaaUninstaller = Join-Path $xaaInstallDir 'uninstall.exe'
    if (Test-Path -LiteralPath $xaaUninstaller) {
        $xaaUninstall = Start-Process -FilePath $xaaUninstaller -ArgumentList '/S' -WindowStyle Hidden -Wait -PassThru
        $xaaReport.uninstaller_exit_code = $xaaUninstall.ExitCode
        for ($xaaAttempt = 0; $xaaAttempt -lt 40 -and (Test-Path -LiteralPath (Join-Path $xaaInstallDir 'xaa-desktop.exe')); $xaaAttempt++) { Start-Sleep -Milliseconds 250 }
        $xaaReport.binaries_removed = -not (Test-Path -LiteralPath (Join-Path $xaaInstallDir 'xaa-desktop.exe')) -and -not (Test-Path -LiteralPath (Join-Path $xaaInstallDir 'xaa-engine.exe'))
    }
    $xaaReport.user_data_preserved = (Test-Path -LiteralPath $xaaMarker) -and (Get-FileHash -LiteralPath $xaaMarker -Algorithm SHA256).Hash -eq $xaaMarkerHash
    $xaaReport.passed = $xaaReport.installed_boot_verified -and $xaaReport.installed_acp_cli_verified -and $xaaReport.installed_release_notices_verified -and $xaaReport.installed_backup_restore_verified -and $xaaReport.binaries_removed -and $xaaReport.user_data_preserved -and $xaaReport.uninstaller_exit_code -eq 0 -and ((-not $xaaPriorInstaller) -or ($xaaReport.upgrade_verified -and $xaaReport.prior_install_verified -and $xaaReport.upgrade_data_verified))
    $xaaReport | ConvertTo-Json | Set-Content -LiteralPath 'build/installer/install-smoke.json' -Encoding utf8
    Remove-Item Env:XAA_NATIVE_ACCEPTANCE_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:XAA_NATIVE_BACKUP_FIXTURE -ErrorAction SilentlyContinue
    $env:LOCALAPPDATA = $xaaPriorLocalAppData
}
if (-not $xaaReport.passed) { throw 'Install/uninstall or data preservation verification failed.' }
