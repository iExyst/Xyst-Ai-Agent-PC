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
$xaaRoamingAppData = [IO.Path]::GetFullPath((Join-Path $env:RUNNER_TEMP 'xaa-isolated-appdata'))
if (-not $xaaRoamingAppData.StartsWith($xaaRunnerRoot, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $xaaRoamingAppData)) {
    throw 'Isolated roaming application data profile must be a new directory under RUNNER_TEMP.'
}
New-Item -ItemType Directory -Path $xaaRoamingAppData | Out-Null
$xaaPriorLocalAppData = $env:LOCALAPPDATA
$xaaPriorAppData = $env:APPDATA
$xaaPriorTestConfigDirectory = $env:XAA_TEST_CONFIG_DIR
$xaaPriorExpectedCloseBehavior = $env:XAA_NATIVE_EXPECT_CLOSE_BEHAVIOR
$xaaPriorNativeDesktopPath = $env:XAA_NATIVE_DESKTOP_PATH
$xaaPriorNativeEnginePath = $env:XAA_NATIVE_ENGINE_PATH
$xaaPriorNativeManifestPath = $env:XAA_NATIVE_MANIFEST_PATH
$xaaPriorNativeStage = $env:XAA_NATIVE_STAGE
$xaaPriorNativeDebugPort = $env:XAA_NATIVE_DEBUG_PORT
$env:LOCALAPPDATA = $xaaLocalAppData
$env:APPDATA = $xaaRoamingAppData
$xaaProductName = (Get-Content -LiteralPath 'apps/desktop/src-tauri/tauri.conf.json' -Raw | ConvertFrom-Json).productName
$xaaInstallDir = [IO.Path]::GetFullPath((Join-Path $xaaLocalAppData $xaaProductName))
$xaaInstallParent = [IO.Path]::GetFullPath($xaaLocalAppData).TrimEnd('\') + '\'
if (-not $xaaInstallDir.StartsWith($xaaInstallParent, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $xaaInstallDir)) {
    throw 'Per-user installer test destination must be a new directory under isolated LOCALAPPDATA.'
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
$xaaDesktop = Join-Path $xaaInstallDir 'xaa-desktop.exe'
$xaaStartupRegistryPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$xaaStartupValueName = 'Xyst AI Agent'
$xaaStartupCommand = '"' + $xaaDesktop + '" --background'
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
$xaaDesktopSettings = Join-Path $xaaRoamingAppData 'dev.xyst.xaa\desktop-settings.json'
$xaaReport = @{ passed = $false; environment = 'disposable hosted Windows runner'; unsigned = $true; install_scope = 'currentUser'; default_install_directory = $xaaInstallDir }
if ($xaaManifest.install_mode -ne 'currentUser') { throw 'Installer manifest does not declare the current-user install mode.' }
$xaaReport.install_scope = $xaaManifest.install_mode
try {
    if ($xaaPriorInstaller) {
        # The archived 0.1.0 installer predates the currentUser release mode and
        # needs an explicit target to seed the upgrade fixture in this profile.
        # The package under test below is installed without /D and must choose
        # its own declared per-user default directory.
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
        $xaaSeededProfile = Get-Content -LiteralPath $xaaBackupFixture -Raw | ConvertFrom-Json
        if ($xaaSeededProfile.seeded_schema_version -ne 18 -or $xaaSeededProfile.schema_downgrade_integrity -ne 'ok' -or $xaaSeededProfile.schema_downgrade_foreign_key_violations -ne 0) {
            throw 'The pre-upgrade database fixture is not a valid schema-18 profile.'
        }
        $xaaReport.upgrade_fixture_schema_version = $xaaSeededProfile.seeded_schema_version
        $xaaReport.upgrade_fixture_integrity = $xaaSeededProfile.schema_downgrade_integrity
        $xaaReport.upgrade_fixture_foreign_key_violations = $xaaSeededProfile.schema_downgrade_foreign_key_violations
        $xaaReport.upgrade_data_fixture_seeded_before_upgrade = $true
        $xaaExistingStartup = Get-Item -LiteralPath $xaaStartupRegistryPath -ErrorAction SilentlyContinue
        if ($xaaExistingStartup -and $xaaExistingStartup.GetValueNames() -contains $xaaStartupValueName) { throw 'Refusing to replace an existing sign-in startup value.' }
        New-Item -Path $xaaStartupRegistryPath -Force | Out-Null
        New-ItemProperty -LiteralPath $xaaStartupRegistryPath -Name $xaaStartupValueName -Value $xaaStartupCommand -PropertyType String | Out-Null
        $xaaReport.startup_entry_seeded_before_upgrade = $true
    }
    [IO.File]::WriteAllText($xaaMarker, 'XAA upgrade preservation fixture')
    $xaaMarkerHash = (Get-FileHash -LiteralPath $xaaMarker -Algorithm SHA256).Hash
    $xaaSetup = Start-Process -FilePath $xaaInstaller -ArgumentList @('/S') -WindowStyle Hidden -Wait -PassThru
    if ($xaaSetup.ExitCode -ne 0) { throw "Installer exit code $($xaaSetup.ExitCode)" }
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
    $xaaReadinessOutput = & uv run python scripts/installed_skill_readiness_diagnostic.py --engine $xaaEngine
    if ($LASTEXITCODE -ne 0) { throw 'Installed Agent Skill dependency readiness acceptance failed.' }
    $xaaReadinessAcceptance = ($xaaReadinessOutput | Out-String | ConvertFrom-Json)
    if (-not $xaaReadinessAcceptance.graceful_shutdown -or $xaaReadinessAcceptance.model_inference_requested -or -not $xaaReadinessAcceptance.dependencies_unresolved -or $xaaReadinessAcceptance.unverified_dependency_count -lt 1 -or 'packaging>=26.3,<27' -notin $xaaReadinessAcceptance.available_python_dependency_specs -or 'packaging==0.0' -notin $xaaReadinessAcceptance.incompatible_python_dependency_specs -or 'xaa-readiness-missing-fixture==99.0' -notin $xaaReadinessAcceptance.missing_python_dependency_specs) {
        throw 'Installed Agent Skill dependency readiness evidence was incomplete.'
    }
    $xaaReport.installed_skill_readiness_acceptance = $xaaReadinessAcceptance
    $xaaReport.default_per_user_install_directory_verified = (Test-Path -LiteralPath $xaaInstallDir) -and $xaaInstallDir.StartsWith($xaaInstallParent, [StringComparison]::OrdinalIgnoreCase)
    if (-not $xaaReport.default_per_user_install_directory_verified) { throw 'Installer did not use the isolated per-user default install directory.' }
    if (-not $xaaPriorInstaller) {
        $xaaExistingStartup = Get-Item -LiteralPath $xaaStartupRegistryPath -ErrorAction SilentlyContinue
        if ($xaaExistingStartup -and $xaaExistingStartup.GetValueNames() -contains $xaaStartupValueName) { throw 'Refusing to replace an existing sign-in startup value.' }
        New-Item -Path $xaaStartupRegistryPath -Force | Out-Null
        New-ItemProperty -LiteralPath $xaaStartupRegistryPath -Name $xaaStartupValueName -Value $xaaStartupCommand -PropertyType String | Out-Null
        $xaaReport.startup_entry_seeded_after_install = $true
    }
    $xaaStartupEntry = (Get-Item -LiteralPath $xaaStartupRegistryPath).GetValue($xaaStartupValueName)
    $xaaReport.startup_entry_preserved_through_upgrade = $xaaStartupEntry -eq $xaaStartupCommand
    if (-not $xaaReport.startup_entry_preserved_through_upgrade) { throw 'The installer did not preserve the sign-in startup entry.' }
    $xaaBrowserOutput = & uv run python scripts/installed_browser_diagnostic.py --engine $xaaEngine
    if ($LASTEXITCODE -ne 0) { throw 'Installed browser worker acceptance failed.' }
    $xaaBrowserAcceptance = ($xaaBrowserOutput | Out-String | ConvertFrom-Json)
    if (-not $xaaBrowserAcceptance.result -or $xaaBrowserAcceptance.result.status -ne 'ok' -or -not $xaaBrowserAcceptance.result.screenshot_sha256) {
        throw 'Installed browser worker evidence was incomplete.'
    }
    $xaaReport.installed_browser_acceptance = $xaaBrowserAcceptance
    foreach ($xaaNoticeName in @('LICENSE', 'THIRD_PARTY_NOTICES.md', 'dependency-inventory.md', 'release-notes-0.1.1.md')) {
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
            New-ItemProperty -LiteralPath $xaaDebugPolicy -Name 'xaa-desktop.exe' -Value '--remote-debugging-port=19222 --use-fake-device-for-media-stream --use-fake-ui-for-media-stream' -PropertyType String | Out-Null
            $xaaDebugPolicyOwned = $true
        }
        $env:XAA_NATIVE_ACCEPTANCE_ROOT = $xaaAcceptanceRoot
        $env:XAA_NATIVE_BACKUP_FIXTURE = $xaaBackupFixture
        $env:XAA_NATIVE_BOOT_RECOVERY = '1'
        $env:XAA_TEST_CONFIG_DIR = Split-Path -Parent $xaaDesktopSettings
        $env:XAA_NATIVE_EXPECT_CLOSE_BEHAVIOR = 'keep_running'
        $env:XAA_NATIVE_BROWSER_CANCEL_DEBUG_PORT = '19222'
        $xaaBrowserCancelOutput = & node scripts/native_browser_cancel_smoke.mjs $xaaDesktop
        $xaaCancelExitCode = $LASTEXITCODE
        if ($xaaCancelExitCode -ne 0) { throw 'Installed desktop browser cancellation acceptance failed.' }
        $xaaCancelAcceptance = ($xaaBrowserCancelOutput | Out-String | ConvertFrom-Json)
        if (-not $xaaCancelAcceptance.passed -or -not $xaaCancelAcceptance.cancel_request_observed -or $xaaCancelAcceptance.final_run_state -ne 'cancelled' -or $xaaCancelAcceptance.saved_browser_result.status -ne 'cancelled' -or -not $xaaCancelAcceptance.installed_microphone_capture.passed -or -not $xaaCancelAcceptance.installed_microphone_capture.microphone_tracks_ended_after_capture -or -not $xaaCancelAcceptance.workspace_task.passed -or $xaaCancelAcceptance.workspace_task.final_run_state -ne 'completed' -or -not $xaaCancelAcceptance.workspace_task.saved_read_contains_fixture_marker -or -not $xaaCancelAcceptance.workspace_task.accepted_through_desktop_review -or -not $xaaCancelAcceptance.launch_credentials_clean -or $xaaCancelAcceptance.model_inference_requested) {
            throw 'Installed desktop browser cancellation evidence was incomplete.'
        }
        $xaaReport.installed_desktop_browser_cancellation = $xaaCancelAcceptance
        & node scripts/native_boot_smoke.mjs $xaaDesktop
        $xaaBootExitCode = $LASTEXITCODE
        if ($xaaBootExitCode -ne 0) { throw 'Installed application boot/shutdown test failed.' }
        $env:XAA_NATIVE_SCHEDULE_RESTART_ROOT = Join-Path $xaaAcceptanceRoot 'native-schedule-restart'
        $xaaScheduleRestartOutput = & node scripts/native_schedule_restart_smoke.mjs $xaaDesktop
        $xaaScheduleRestartExitCode = $LASTEXITCODE
        if ($xaaScheduleRestartExitCode -ne 0) { throw 'Installed scheduled cold-restart acceptance failed.' }
        $xaaScheduleRestartAcceptance = ($xaaScheduleRestartOutput | Out-String | ConvertFrom-Json)
        if (-not $xaaScheduleRestartAcceptance.passed -or $xaaScheduleRestartAcceptance.full_desktop_process_restarts -ne 2 -or $xaaScheduleRestartAcceptance.state_after_both_starts -ne 'missed' -or $xaaScheduleRestartAcceptance.execution_count_after_both_starts -ne 1 -or $xaaScheduleRestartAcceptance.run_count -ne 0 -or $xaaScheduleRestartAcceptance.model_inference_requested -or $xaaScheduleRestartAcceptance.telegram_delivery_requested) {
            throw 'Installed scheduled cold-restart evidence was incomplete.'
        }
        $xaaReport.native_schedule_restart = $xaaScheduleRestartAcceptance
        $xaaRestartDebugPort = 19227
        $env:XAA_NATIVE_DEBUG_PORT = [string]$xaaRestartDebugPort
        if ($xaaElevated) {
            Set-ItemProperty -LiteralPath $xaaDebugPolicy -Name 'xaa-desktop.exe' -Value "--remote-debugging-port=$xaaRestartDebugPort"
        }
        $xaaStreamRestartOutput = & node scripts/native_restart_smoke.mjs $xaaDesktop
        $xaaStreamRestartExitCode = $LASTEXITCODE
        if ($xaaStreamRestartExitCode -ne 0) { throw 'Installed desktop streamed-output restart acceptance failed.' }
        $xaaStreamRestartAcceptance = Get-Content -LiteralPath 'build/installer/native-restart-acceptance.json' -Raw | ConvertFrom-Json
        if (-not $xaaStreamRestartAcceptance.passed -or -not $xaaStreamRestartAcceptance.streamed_command_output_restored_after_restart -or -not $xaaStreamRestartAcceptance.restored_command_output_marker -or $xaaStreamRestartAcceptance.candidate_count -ne 1 -or $xaaStreamRestartAcceptance.final_state -ne 'verifying' -or $xaaStreamRestartAcceptance.tool_effects -ne 0 -or -not $xaaStreamRestartAcceptance.workspace_unchanged -or -not $xaaStreamRestartAcceptance.launch_credentials_cleaned -or $xaaStreamRestartAcceptance.model_requests -lt 1 -or $xaaStreamRestartAcceptance.desktop_sha256 -ne $xaaDesktopHash -or $xaaStreamRestartAcceptance.engine_sha256 -ne $xaaEngineHash) {
            throw 'Installed desktop streamed-output restart evidence was incomplete or did not use the installed binaries.'
        }
        $xaaReport.installed_streamed_output_restart = $xaaStreamRestartAcceptance

        # Exercise MCP configuration, process limits and AppContainer isolation using the
        # actual binaries installed by the unsigned package, not the build stage.
        $xaaMcpDebugPort = 19228
        $env:XAA_NATIVE_STAGE = 'native-release'
        $env:XAA_NATIVE_DEBUG_PORT = [string]$xaaMcpDebugPort
        $env:XAA_NATIVE_DESKTOP_PATH = $xaaDesktop
        $env:XAA_NATIVE_ENGINE_PATH = $xaaEngine
        $env:XAA_NATIVE_MANIFEST_PATH = (Resolve-Path -LiteralPath 'build/installer/manifest.json').Path
        if ($xaaElevated) {
            Set-ItemProperty -LiteralPath $xaaDebugPolicy -Name 'xaa-desktop.exe' -Value "--remote-debugging-port=$xaaMcpDebugPort"
        }
        $xaaMcpOutput = & node scripts/native_mcp_smoke.mjs
        if ($LASTEXITCODE -ne 0) { throw 'Installed desktop MCP process-limit acceptance failed.' }
        $xaaMcpAcceptance = ($xaaMcpOutput | Out-String | ConvertFrom-Json)
        $xaaMcpLimits = $xaaMcpAcceptance.native_process_limits
        $xaaMcpIsolation = $xaaMcpAcceptance.network_isolation
        if (-not $xaaMcpAcceptance.passed -or -not $xaaMcpAcceptance.real_fixture_process -or $xaaMcpAcceptance.desktop_sha256 -ne $xaaDesktopHash -or $xaaMcpAcceptance.engine_sha256 -ne $xaaEngineHash -or $xaaMcpLimits.default_memory_limit_mb -ne 2048 -or $xaaMcpLimits.default_cpu_limit_percent -ne 80 -or $xaaMcpLimits.edited_memory_limit_mb -ne 1536 -or $xaaMcpLimits.edited_cpu_limit_percent -ne 70 -or -not $xaaMcpLimits.retained_after_connection -or -not $xaaMcpIsolation.persisted -or -not $xaaMcpIsolation.parent_loopback_reachable -or -not $xaaMcpIsolation.isolated_server_blocked -or -not $xaaMcpIsolation.disconnected_cleanly -or $xaaMcpAcceptance.model_inference_requested) {
            throw 'Installed MCP process-limit or AppContainer network-isolation evidence was incomplete or did not use the installed binaries.'
        }
        $xaaReport.installed_mcp_process_limits = $xaaMcpAcceptance
        $xaaReport.installed_mcp_network_isolation = $xaaMcpIsolation
    } finally {
        if ($xaaDebugPolicyOwned) {
            Remove-ItemProperty -LiteralPath $xaaDebugPolicy -Name 'xaa-desktop.exe'
            $xaaReport.debugging_configuration_removed = -not ((Get-Item -LiteralPath $xaaDebugPolicy).GetValueNames() -contains 'xaa-desktop.exe')
            if (-not $xaaReport.debugging_configuration_removed) { throw 'App debugging configuration cleanup failed.' }
        }
    }
    if ($xaaBootExitCode -ne 0) { throw 'Installed application boot/shutdown test failed.' }
    $xaaReport.native_acceptance = Get-Content -LiteralPath (Join-Path $xaaAcceptanceRoot 'report.json') -Raw | ConvertFrom-Json
    if ($xaaReport.native_acceptance.initial_close_behavior -ne 'keep_running') { throw 'Installed desktop did not load its isolated default close behavior.' }
    $xaaReport.installed_backup_restore_verified = [bool]$xaaReport.native_acceptance.installed_backup_restore_verified
    if (-not $xaaReport.installed_backup_restore_verified -or -not $xaaReport.native_acceptance.backup_acceptance.verification.passed) { throw 'Installed backup/restore evidence was incomplete.' }
    if (-not $xaaReport.native_acceptance.owner_reviewed_startup_recovery.passed -or -not $xaaReport.native_acceptance.owner_reviewed_startup_recovery.cold_desktop_start -or -not $xaaReport.native_acceptance.owner_reviewed_startup_recovery.owner_recovery_confirmed_in_ui -or $xaaReport.native_acceptance.owner_reviewed_startup_recovery.resolved_call_ids.Count -ne 3 -or $xaaReport.native_acceptance.owner_reviewed_startup_recovery.automatic_resume) { throw 'Installed desktop startup recovery did not restore and complete all owner-reviewed writes on cold start.' }
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
    $xaaStartupKeyAfterUninstall = Get-Item -LiteralPath $xaaStartupRegistryPath -ErrorAction SilentlyContinue
    $xaaReport.startup_entry_removed_on_uninstall = -not $xaaStartupKeyAfterUninstall -or $xaaStartupValueName -notin $xaaStartupKeyAfterUninstall.GetValueNames()
    $xaaReport.user_data_preserved = (Test-Path -LiteralPath $xaaMarker) -and (Get-FileHash -LiteralPath $xaaMarker -Algorithm SHA256).Hash -eq $xaaMarkerHash
    $xaaReport.desktop_settings_preserved = $false
    if (Test-Path -LiteralPath $xaaDesktopSettings) {
        try { $xaaReport.desktop_settings_preserved = (Get-Content -LiteralPath $xaaDesktopSettings -Raw | ConvertFrom-Json).close_behavior -eq 'exit_on_close' } catch { $xaaReport.desktop_settings_preserved = $false }
    }
    $xaaReport.passed = $xaaReport.default_per_user_install_directory_verified -and $xaaReport.installed_boot_verified -and $xaaReport.native_schedule_restart.passed -and $xaaReport.installed_streamed_output_restart.passed -and $xaaReport.installed_desktop_browser_cancellation.passed -and $xaaReport.installed_mcp_process_limits.passed -and $xaaReport.installed_acp_cli_verified -and $xaaReport.installed_skill_readiness_acceptance.graceful_shutdown -and -not $xaaReport.installed_skill_readiness_acceptance.model_inference_requested -and $xaaReport.installed_browser_acceptance.result.status -eq 'ok' -and $xaaReport.installed_release_notices_verified -and $xaaReport.installed_backup_restore_verified -and $xaaReport.startup_entry_preserved_through_upgrade -and $xaaReport.startup_entry_removed_on_uninstall -and $xaaReport.binaries_removed -and $xaaReport.user_data_preserved -and $xaaReport.desktop_settings_preserved -and $xaaReport.uninstaller_exit_code -eq 0 -and ((-not $xaaPriorInstaller) -or ($xaaReport.upgrade_verified -and $xaaReport.prior_install_verified -and $xaaReport.upgrade_data_verified -and $xaaReport.startup_entry_seeded_before_upgrade))
    $xaaReport | ConvertTo-Json | Set-Content -LiteralPath 'build/installer/install-smoke.json' -Encoding utf8
    if ($xaaReport.startup_entry_seeded_before_upgrade -or $xaaReport.startup_entry_seeded_after_install) {
        $xaaStartupKeyForCleanup = Get-Item -LiteralPath $xaaStartupRegistryPath -ErrorAction SilentlyContinue
        if ($xaaStartupKeyForCleanup -and $xaaStartupValueName -in $xaaStartupKeyForCleanup.GetValueNames()) {
            Remove-ItemProperty -LiteralPath $xaaStartupRegistryPath -Name $xaaStartupValueName
        }
    }
    Remove-Item Env:XAA_NATIVE_ACCEPTANCE_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:XAA_NATIVE_BACKUP_FIXTURE -ErrorAction SilentlyContinue
    Remove-Item Env:XAA_NATIVE_SCHEDULE_RESTART_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:XAA_NATIVE_DEBUG_PORT -ErrorAction SilentlyContinue
    if ($null -eq $xaaPriorNativeDesktopPath) { Remove-Item Env:XAA_NATIVE_DESKTOP_PATH -ErrorAction SilentlyContinue } else { $env:XAA_NATIVE_DESKTOP_PATH = $xaaPriorNativeDesktopPath }
    if ($null -eq $xaaPriorNativeEnginePath) { Remove-Item Env:XAA_NATIVE_ENGINE_PATH -ErrorAction SilentlyContinue } else { $env:XAA_NATIVE_ENGINE_PATH = $xaaPriorNativeEnginePath }
    if ($null -eq $xaaPriorNativeManifestPath) { Remove-Item Env:XAA_NATIVE_MANIFEST_PATH -ErrorAction SilentlyContinue } else { $env:XAA_NATIVE_MANIFEST_PATH = $xaaPriorNativeManifestPath }
    if ($null -eq $xaaPriorNativeStage) { Remove-Item Env:XAA_NATIVE_STAGE -ErrorAction SilentlyContinue } else { $env:XAA_NATIVE_STAGE = $xaaPriorNativeStage }
    if ($null -eq $xaaPriorNativeDebugPort) { Remove-Item Env:XAA_NATIVE_DEBUG_PORT -ErrorAction SilentlyContinue } else { $env:XAA_NATIVE_DEBUG_PORT = $xaaPriorNativeDebugPort }
    if ($null -eq $xaaPriorTestConfigDirectory) { Remove-Item Env:XAA_TEST_CONFIG_DIR -ErrorAction SilentlyContinue } else { $env:XAA_TEST_CONFIG_DIR = $xaaPriorTestConfigDirectory }
    if ($null -eq $xaaPriorExpectedCloseBehavior) { Remove-Item Env:XAA_NATIVE_EXPECT_CLOSE_BEHAVIOR -ErrorAction SilentlyContinue } else { $env:XAA_NATIVE_EXPECT_CLOSE_BEHAVIOR = $xaaPriorExpectedCloseBehavior }
    $env:LOCALAPPDATA = $xaaPriorLocalAppData
    $env:APPDATA = $xaaPriorAppData
}
if (-not $xaaReport.passed) { throw 'Install/uninstall or data preservation verification failed.' }
