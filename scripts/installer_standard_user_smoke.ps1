param(
    [switch]$StandardUserChild,
    [string]$ConfigPath
)

$ErrorActionPreference = 'Stop'

if ($StandardUserChild) {
    $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    $report = @{
        passed = $false
        standard_user = $true
        account_is_administrator = $null
        install_scope = 'currentUser'
        model_inference_requested = $false
    }
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = [Security.Principal.WindowsPrincipal]::new($identity)
        $report.account_sid = $identity.User.Value
        $report.account_is_administrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        if ($identity.User.Value -ne $config.account_sid) { throw 'The installer child did not start under the created standard-account SID.' }
        $loadedProfile = Get-CimInstance -ClassName Win32_UserProfile -Filter "SID='$($identity.User.Value)'" -ErrorAction Stop
        if (-not $loadedProfile -or -not $loadedProfile.LocalPath) { throw 'Windows did not load a profile for the standard account.' }
        $profileRoot = [IO.Path]::GetFullPath($loadedProfile.LocalPath).TrimEnd('\')
        $expectedProfileRoot = [IO.Path]::GetFullPath($config.expected_profile_path).TrimEnd('\')
        $report.account_profile_verified = $profileRoot -eq $expectedProfileRoot -and (Test-Path -LiteralPath $profileRoot)
        if (-not $report.account_profile_verified) { throw 'The loaded Windows profile does not match the disposable account.' }
        # CreateProcessWithLogonW loads the profile but inherits the caller's
        # environment block. Derive the standard profile paths from the SID's
        # Windows profile record before asking NSIS where it installed.
        $env:USERPROFILE = $profileRoot
        $env:LOCALAPPDATA = Join-Path $profileRoot 'AppData\Local'
        $env:APPDATA = Join-Path $profileRoot 'AppData\Roaming'
        $report.user_profile = $env:USERPROFILE
        $report.local_app_data = $env:LOCALAPPDATA
        $report.roaming_app_data = $env:APPDATA
        if (-not (Test-Path -LiteralPath $env:LOCALAPPDATA) -or -not (Test-Path -LiteralPath $env:APPDATA)) {
            throw 'The loaded standard-account profile is missing its local or roaming application-data directory.'
        }
        if ($report.account_is_administrator) { throw 'The acceptance account unexpectedly has administrator rights.' }

        $setup = Start-Process -FilePath $config.installer_path -ArgumentList @('/S') -WindowStyle Hidden -Wait -PassThru
        $report.installer_exit_code = $setup.ExitCode
        if ($setup.ExitCode -ne 0) { throw "Per-user installer exited with code $($setup.ExitCode)." }

        $installDirectory = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA $config.product_name))
        $expectedDirectory = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Xyst AI Agent'))
        $report.install_directory = $installDirectory
        $report.default_per_user_directory_verified = $installDirectory -eq $expectedDirectory -and (Test-Path -LiteralPath $installDirectory)
        if (-not $report.default_per_user_directory_verified) { throw 'The standard-user install did not use the default per-user directory.' }

        $desktop = Join-Path $installDirectory 'xaa-desktop.exe'
        $engine = Join-Path $installDirectory 'xaa-engine.exe'
        if (-not (Test-Path -LiteralPath $desktop) -or -not (Test-Path -LiteralPath $engine)) {
            throw 'The standard-user install is missing the desktop or bundled engine.'
        }
        $report.installed_hashes = @{
            desktop = (Get-FileHash -LiteralPath $desktop -Algorithm SHA256).Hash
            engine = (Get-FileHash -LiteralPath $engine -Algorithm SHA256).Hash
        }
        $report.expected_hashes = $config.expected_hashes
        $report.installed_binary_hashes_match = $report.installed_hashes.desktop -eq $config.expected_hashes.desktop -and $report.installed_hashes.engine -eq $config.expected_hashes.engine
        if (-not $report.installed_binary_hashes_match) { throw 'Standard-user installed binary hashes did not match the release manifest.' }

        $acpHelp = & $engine --cli acp --help 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -or $acpHelp -notmatch 'usage: xaa acp') {
            throw 'The bundled engine CLI could not run under the standard account.'
        }
        $report.bundled_engine_cli_verified = $true

        $dataDirectory = Join-Path $env:LOCALAPPDATA 'XAA'
        $configDirectory = Join-Path $env:APPDATA 'dev.xyst.xaa'
        $dataMarker = Join-Path $dataDirectory 'standard-user-preservation.txt'
        $configMarker = Join-Path $configDirectory 'standard-user-preservation.txt'
        if ((Test-Path -LiteralPath $dataMarker) -or (Test-Path -LiteralPath $configMarker)) {
            throw 'The fresh standard-user profile already contains the acceptance markers.'
        }
        New-Item -ItemType Directory -Path $dataDirectory, $configDirectory -Force | Out-Null
        [IO.File]::WriteAllText($dataMarker, 'Disposable standard-user install fixture')
        [IO.File]::WriteAllText($configMarker, 'Disposable standard-user config fixture')

        $uninstaller = Join-Path $installDirectory 'uninstall.exe'
        if (-not (Test-Path -LiteralPath $uninstaller)) { throw 'The standard-user install is missing its uninstaller.' }
        $uninstall = Start-Process -FilePath $uninstaller -ArgumentList @('/S') -WindowStyle Hidden -Wait -PassThru
        $report.uninstaller_exit_code = $uninstall.ExitCode
        if ($uninstall.ExitCode -ne 0) { throw "Standard-user uninstaller exited with code $($uninstall.ExitCode)." }
        $report.binaries_removed = -not (Test-Path -LiteralPath $desktop) -and -not (Test-Path -LiteralPath $engine)
        $report.app_data_preserved = (Test-Path -LiteralPath $dataMarker) -and (Get-Content -LiteralPath $dataMarker -Raw) -eq 'Disposable standard-user install fixture'
        $report.config_preserved = (Test-Path -LiteralPath $configMarker) -and (Get-Content -LiteralPath $configMarker -Raw) -eq 'Disposable standard-user config fixture'
        $report.passed = $report.standard_user -and $report.account_profile_verified -and -not $report.account_is_administrator -and $report.default_per_user_directory_verified -and $report.installed_binary_hashes_match -and $report.bundled_engine_cli_verified -and $report.binaries_removed -and $report.app_data_preserved -and $report.config_preserved -and $report.uninstaller_exit_code -eq 0
    } catch {
        $report.error = $_.Exception.Message
    } finally {
        $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $config.report_path -Encoding utf8
    }
    if (-not $report.passed) { exit 1 }
    exit 0
}

if ($env:GITHUB_ACTIONS -ne 'true' -or -not $env:RUNNER_TEMP) {
    throw 'This standard-account installer acceptance is restricted to a disposable GitHub Actions runner.'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$runnerPrincipal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $runnerPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'The hosted runner must be able to create and remove a disposable standard account.'
}

$runnerRoot = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
$root = [IO.Path]::GetFullPath((Join-Path $env:RUNNER_TEMP ('xaa-standard-user-' + [guid]::NewGuid().ToString('N'))))
if (-not $root.StartsWith($runnerRoot, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $root)) {
    throw 'Standard-user fixture root must be new and contained under RUNNER_TEMP.'
}
$manifest = Get-Content -LiteralPath 'build/installer/manifest.json' -Raw | ConvertFrom-Json
if ($manifest.install_mode -ne 'currentUser') { throw 'Release manifest does not declare currentUser installation.' }
$installerName = $manifest.artifacts.installer.filename
if ([IO.Path]::GetFileName($installerName) -ne $installerName) { throw 'Installer manifest filename escaped its staging directory.' }
$sourceInstaller = (Resolve-Path -LiteralPath (Join-Path 'build/installer' $installerName)).Path
if ((Get-FileHash -LiteralPath $sourceInstaller -Algorithm SHA256).Hash -ne $manifest.artifacts.installer.sha256) {
    throw 'Installer checksum mismatch.'
}
$productName = (Get-Content -LiteralPath 'apps/desktop/src-tauri/tauri.conf.json' -Raw | ConvertFrom-Json).productName
if ($productName -ne 'Xyst AI Agent') { throw 'Standard-user smoke has not been updated for the configured product name.' }
$username = 'xaa_ci_' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$account = $null
$profile = $null
$failure = $null
$reportPath = Join-Path $root 'report.json'

try {
    if (Get-LocalUser -Name $username -ErrorAction SilentlyContinue) { throw 'Random standard-user name already exists.' }
    $expectedProfilePath = [IO.Path]::GetFullPath((Join-Path (Join-Path $env:SystemDrive 'Users') $username)).TrimEnd('\')
    if (Test-Path -LiteralPath $expectedProfilePath) { throw 'Random standard-user profile path already exists.' }
    New-Item -ItemType Directory -Path $root | Out-Null
    $randomBytes = [byte[]]::new(32)
    [Security.Cryptography.RandomNumberGenerator]::Fill($randomBytes)
    $passwordText = [Convert]::ToBase64String($randomBytes) + 'aA9!'
    [Array]::Clear($randomBytes, 0, $randomBytes.Length)
    $password = ConvertTo-SecureString -String $passwordText -AsPlainText -Force
    $passwordText = $null
    $account = New-LocalUser -Name $username -Password $password -PasswordNeverExpires -UserMayNotChangePassword -Description 'XAA installer acceptance'
    $sid = $account.SID.Value

    $acl = Get-Acl -LiteralPath $root
    $userRule = [Security.AccessControl.FileSystemAccessRule]::new(
        $account.SID,
        [Security.AccessControl.FileSystemRights]::Modify,
        [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow
    )
    $acl.SetAccessRule($userRule)
    Set-Acl -LiteralPath $root -AclObject $acl

    $installerCopy = Join-Path $root $installerName
    Copy-Item -LiteralPath $sourceInstaller -Destination $installerCopy
    Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $root 'installer_standard_user_smoke.ps1')
    $scriptCopy = Join-Path $root 'installer_standard_user_smoke.ps1'
    $configPath = Join-Path $root 'config.json'
    @{
        installer_path = $installerCopy
        account_sid = $sid
        expected_profile_path = $expectedProfilePath
        product_name = $productName
        expected_hashes = @{
            desktop = $manifest.artifacts.desktop.sha256
            engine = $manifest.artifacts.engine.sha256
        }
        report_path = $reportPath
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding utf8

    $usersSid = (Get-LocalGroup -SID 'S-1-5-32-545').SID.Value
    $adminsSid = (Get-LocalGroup -SID 'S-1-5-32-544').SID.Value
    $membership = @(Get-LocalGroupMember -SID $usersSid -ErrorAction SilentlyContinue | Where-Object { $_.SID.Value -eq $sid })
    if ($membership.Count -eq 0) { Add-LocalGroupMember -SID $usersSid -Member $account }
    $adminMembership = @(Get-LocalGroupMember -SID $adminsSid -ErrorAction SilentlyContinue | Where-Object { $_.SID.Value -eq $sid })
    if ($adminMembership.Count -ne 0) { throw 'Disposable acceptance user was added to Administrators.' }

    $credential = [PSCredential]::new("$env:COMPUTERNAME\$username", $password)
    $scriptLiteral = "'" + $scriptCopy.Replace("'", "''") + "'"
    $configLiteral = "'" + $configPath.Replace("'", "''") + "'"
    $payload = "& $scriptLiteral -StandardUserChild -ConfigPath $configLiteral"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($payload))
    $pwsh = Join-Path $PSHOME 'pwsh.exe'
    $child = Start-Process -FilePath $pwsh -Credential $credential -LoadUserProfile -ArgumentList @('-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', $encoded) -WorkingDirectory $env:SystemRoot -WindowStyle Hidden -Wait -PassThru
    if (-not (Test-Path -LiteralPath $reportPath)) { throw "Standard-user acceptance produced no report (exit $($child.ExitCode))." }
    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    $report | Add-Member -NotePropertyName runner_account_is_administrator -NotePropertyValue $true -Force
    $report | Add-Member -NotePropertyName acceptance_account_sid_matches -NotePropertyValue ($report.account_sid -eq $sid) -Force
    $report.passed = [bool]$report.passed -and $report.acceptance_account_sid_matches -and $child.ExitCode -eq 0
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath 'build/installer/standard-user-smoke.json' -Encoding utf8
    if (-not $report.passed) { $failure = "Standard-user install acceptance failed: $($report.error)" }
} catch {
    $failure = $_.Exception.Message
    if (Test-Path -LiteralPath $reportPath) {
        try { Copy-Item -LiteralPath $reportPath -Destination 'build/installer/standard-user-smoke.json' -Force } catch { }
    }
} finally {
    $cleanupErrors = [System.Collections.Generic.List[string]]::new()
    if ($account) {
        try {
            $profile = Get-CimInstance -ClassName Win32_UserProfile -Filter "SID='$($account.SID.Value)'" -ErrorAction SilentlyContinue
            if ($profile) {
                $usersRoot = [IO.Path]::GetFullPath((Join-Path $env:SystemDrive 'Users')).TrimEnd('\') + '\'
                $profilePath = [IO.Path]::GetFullPath($profile.LocalPath).TrimEnd('\')
                $allowedProfilePrefix = $expectedProfilePath + '.'
                if (-not $profilePath.StartsWith($usersRoot, [StringComparison]::OrdinalIgnoreCase) -or ($profilePath -ne $expectedProfilePath -and -not $profilePath.StartsWith($allowedProfilePrefix, [StringComparison]::OrdinalIgnoreCase))) {
                    throw 'Refusing to delete a profile outside the newly created standard-user path.'
                }
                Remove-CimInstance -InputObject $profile
                $profilePath = [IO.Path]::GetFullPath($profile.LocalPath).TrimEnd('\')
                if (Test-Path -LiteralPath $profilePath) { Remove-Item -LiteralPath $profilePath -Recurse -Force }
            }
        } catch {
            $cleanupErrors.Add("Profile cleanup failed: $($_.Exception.Message)")
        }
        try {
            $remainingAccount = Get-LocalUser -Name $username -ErrorAction SilentlyContinue
            if ($remainingAccount) { Remove-LocalUser -Name $username }
        } catch {
            $cleanupErrors.Add("Account cleanup failed: $($_.Exception.Message)")
        }
    }
    if (Test-Path -LiteralPath $root) {
        $resolvedRoot = [IO.Path]::GetFullPath($root).TrimEnd('\')
        if (-not $resolvedRoot.StartsWith($runnerRoot, [StringComparison]::OrdinalIgnoreCase) -or $resolvedRoot -notmatch '\\xaa-standard-user-[0-9a-f]{32}$') {
            if ($failure) { $failure += ' Fixture cleanup was refused because the path guard failed.' } else { $failure = 'Fixture cleanup was refused because the path guard failed.' }
        } else {
            try { Remove-Item -LiteralPath $resolvedRoot -Recurse -Force } catch { $cleanupErrors.Add("Fixture cleanup failed: $($_.Exception.Message)") }
        }
    }
    if ($cleanupErrors.Count -gt 0) {
        $failureMessage = $cleanupErrors -join ' '
        if ($failure) { $failure += " $failureMessage" } else { $failure = $failureMessage }
        $savedReport = 'build/installer/standard-user-smoke.json'
        if (Test-Path -LiteralPath $savedReport) {
            try {
                $finalReport = Get-Content -LiteralPath $savedReport -Raw | ConvertFrom-Json
                $finalReport.cleanup_errors = @($cleanupErrors)
                $finalReport.passed = $false
                $finalReport | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $savedReport -Encoding utf8
            } catch { }
        }
    }
}

if ($failure) { throw $failure }
