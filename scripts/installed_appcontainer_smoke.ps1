param([string]$AcceptanceId = [guid]::NewGuid().ToString('N'))
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$acceptanceParent = [IO.Path]::GetFullPath((Join-Path $workspace '.acceptance')).TrimEnd('\') + '\'
if ($AcceptanceId -notmatch '^[A-Za-z0-9-]{1,80}$') { throw 'Acceptance ID contains unsupported characters.' }
$root = [IO.Path]::GetFullPath((Join-Path $acceptanceParent ('installed-appcontainer-' + $AcceptanceId)))
$install = Join-Path $root 'install'
if (-not $root.StartsWith($acceptanceParent, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $root)) {
    throw 'Acceptance output must be a new directory under .acceptance.'
}
New-Item -ItemType Directory -Path $root | Out-Null
$report = @{ passed = $false; install_path = $install; readiness = $null; command = $null }
$uninstaller = Join-Path $install 'uninstall.exe'
try {
    $manifest = Get-Content -LiteralPath (Join-Path $workspace 'build/installer/manifest.json') -Raw | ConvertFrom-Json
    $installerName = $manifest.artifacts.installer.filename
    if ([IO.Path]::GetFileName($installerName) -ne $installerName) { throw 'Installer manifest filename escaped its staging directory.' }
    $installer = (Resolve-Path -LiteralPath (Join-Path $workspace (Join-Path 'build/installer' $installerName))).Path
    if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne $manifest.artifacts.installer.sha256) { throw 'Installer checksum mismatch.' }
    $setup = Start-Process -FilePath $installer -ArgumentList @('/S', "/D=$install") -WindowStyle Hidden -Wait -PassThru
    if ($setup.ExitCode -ne 0) { throw "Installer exit code $($setup.ExitCode)." }
    $engine = Join-Path $install 'xaa-engine.exe'
    $desktop = Join-Path $install 'xaa-desktop.exe'
    if (-not (Test-Path -LiteralPath $engine) -or -not (Test-Path -LiteralPath $desktop)) { throw 'Installed desktop or engine is missing.' }
    foreach ($binary in @(@{ path = $engine; expected = $manifest.artifacts.engine.sha256 }, @{ path = $desktop; expected = $manifest.artifacts.desktop.sha256 })) {
        if ((Get-FileHash -LiteralPath $binary.path -Algorithm SHA256).Hash -ne $binary.expected) { throw "Installed binary hash mismatch: $($binary.path)." }
    }
    $report.installed_binary_hashes_match = $true
    $readinessReport = Join-Path $root 'readiness/report.json'
    & uv run python (Join-Path $workspace 'scripts/frozen_windows_appcontainer_readiness.py') --engine $engine --report $readinessReport
    if ($LASTEXITCODE -ne 0) { throw 'Installed engine readiness acceptance failed.' }
    $report.readiness = Get-Content -LiteralPath $readinessReport -Raw | ConvertFrom-Json
    if (-not $report.readiness.passed -or $report.readiness.engine_sha256 -ne $manifest.artifacts.engine.sha256 -or $report.readiness.checks -notcontains 'nested_snapshot_access') { throw 'Installed engine readiness report did not verify nested access.' }
    $commandReport = Join-Path $root 'command/report.json'
    & uv run python (Join-Path $workspace 'scripts/frozen_windows_appcontainer_command.py') --engine $engine --report $commandReport
    if ($LASTEXITCODE -ne 0) { throw 'Installed engine coordinator command acceptance failed.' }
    $report.command = Get-Content -LiteralPath $commandReport -Raw | ConvertFrom-Json
    if (-not $report.command.passed -or -not $report.command.nested_snapshot_access_verified -or -not $report.command.original_workspace_unchanged -or -not $report.command.outside_sentinel_unchanged) { throw 'Installed engine command report did not verify snapshot isolation.' }
    $report.installed_engine_readiness_verified = $true
    $report.installed_coordinator_command_verified = $true
} finally {
    if (Test-Path -LiteralPath $uninstaller) {
        $uninstall = Start-Process -FilePath $uninstaller -ArgumentList '/S' -WindowStyle Hidden -Wait -PassThru
        $report.uninstaller_exit_code = $uninstall.ExitCode
        $report.binaries_removed = -not (Test-Path -LiteralPath (Join-Path $install 'xaa-desktop.exe')) -and -not (Test-Path -LiteralPath (Join-Path $install 'xaa-engine.exe'))
    } else {
        $report.binaries_removed = $false
    }
    $report.passed = $report.installed_binary_hashes_match -and $report.installed_engine_readiness_verified -and $report.installed_coordinator_command_verified -and $report.binaries_removed -and $report.uninstaller_exit_code -eq 0
    $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $root 'report.json') -Encoding utf8
}
if (-not $report.passed) { throw "Installed AppContainer acceptance failed. Evidence: $root" }
Write-Output "Installed AppContainer acceptance passed: $root"
