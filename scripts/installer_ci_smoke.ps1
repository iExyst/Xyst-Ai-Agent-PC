$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or -not $env:RUNNER_TEMP) {
    throw 'This install/uninstall test is restricted to a disposable GitHub Actions runner.'
}
$xaaRunnerRoot = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
$xaaInstallDir = [IO.Path]::GetFullPath((Join-Path $env:RUNNER_TEMP 'xaa-installer-test'))
if (-not $xaaInstallDir.StartsWith($xaaRunnerRoot, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $xaaInstallDir)) {
    throw 'Installer test destination must be a new directory under RUNNER_TEMP.'
}
$xaaManifest = Get-Content -LiteralPath 'build/installer/manifest.json' -Raw | ConvertFrom-Json
$xaaInstallerName = $xaaManifest.artifacts.installer.filename
if ([IO.Path]::GetFileName($xaaInstallerName) -ne $xaaInstallerName) { throw 'Installer manifest filename escaped its staging directory.' }
$xaaInstaller = (Resolve-Path -LiteralPath (Join-Path 'build/installer' $xaaInstallerName)).Path
if ((Get-FileHash -LiteralPath $xaaInstaller -Algorithm SHA256).Hash -ne $xaaManifest.artifacts.installer.sha256) { throw 'Installer checksum mismatch.' }
$xaaDataDir = Join-Path $env:LOCALAPPDATA 'XAA'
$xaaMarker = Join-Path $xaaDataDir 'installer-preservation-fixture.txt'
if (Test-Path -LiteralPath $xaaMarker) { throw 'Data fixture already exists.' }
New-Item -ItemType Directory -Path $xaaDataDir -Force | Out-Null
[IO.File]::WriteAllText($xaaMarker, 'XAA installer preservation fixture')
$xaaMarkerHash = (Get-FileHash -LiteralPath $xaaMarker -Algorithm SHA256).Hash
$xaaReport = @{ passed = $false; environment = 'disposable hosted Windows runner'; unsigned = $true }
try {
    $xaaSetup = Start-Process -FilePath $xaaInstaller -ArgumentList @('/S', "/D=$xaaInstallDir") -WindowStyle Hidden -Wait -PassThru
    if ($xaaSetup.ExitCode -ne 0) { throw "Installer exit code $($xaaSetup.ExitCode)" }
    $xaaDesktop = Join-Path $xaaInstallDir 'xaa-desktop.exe'
    $xaaEngine = Join-Path $xaaInstallDir 'xaa-engine.exe'
    if (-not (Test-Path -LiteralPath $xaaDesktop) -or -not (Test-Path -LiteralPath $xaaEngine)) { throw 'Installed desktop or bundled engine missing.' }
    if ((Get-FileHash -LiteralPath $xaaDesktop -Algorithm SHA256).Hash -ne $xaaManifest.artifacts.desktop.sha256 -or (Get-FileHash -LiteralPath $xaaEngine -Algorithm SHA256).Hash -ne $xaaManifest.artifacts.engine.sha256) { throw 'Installed binary checksums do not match the manifest.' }
    $xaaReport.installed_binary_hashes_match = $true
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
    $xaaReport.installed_binaries_present = $true
    $xaaReport.installed_boot_verified = $true
} finally {
    $xaaUninstaller = Join-Path $xaaInstallDir 'uninstall.exe'
    if (Test-Path -LiteralPath $xaaUninstaller) {
        $xaaUninstall = Start-Process -FilePath $xaaUninstaller -ArgumentList '/S' -WindowStyle Hidden -Wait -PassThru
        $xaaReport.uninstaller_exit_code = $xaaUninstall.ExitCode
        for ($xaaAttempt = 0; $xaaAttempt -lt 40 -and (Test-Path -LiteralPath (Join-Path $xaaInstallDir 'xaa-desktop.exe')); $xaaAttempt++) { Start-Sleep -Milliseconds 250 }
        $xaaReport.binaries_removed = -not (Test-Path -LiteralPath (Join-Path $xaaInstallDir 'xaa-desktop.exe')) -and -not (Test-Path -LiteralPath (Join-Path $xaaInstallDir 'xaa-engine.exe'))
    }
    $xaaReport.user_data_preserved = (Test-Path -LiteralPath $xaaMarker) -and (Get-FileHash -LiteralPath $xaaMarker -Algorithm SHA256).Hash -eq $xaaMarkerHash
    $xaaReport.passed = $xaaReport.installed_boot_verified -and $xaaReport.binaries_removed -and $xaaReport.user_data_preserved -and $xaaReport.uninstaller_exit_code -eq 0
    $xaaReport | ConvertTo-Json | Set-Content -LiteralPath 'build/installer/install-smoke.json' -Encoding utf8
}
if (-not $xaaReport.passed) { throw 'Install/uninstall or data preservation verification failed.' }
