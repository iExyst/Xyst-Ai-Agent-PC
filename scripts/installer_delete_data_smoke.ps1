$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or -not $env:RUNNER_TEMP) {
    throw 'This uninstall data-removal test is restricted to a disposable GitHub Actions runner.'
}

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class XaaInstallerWindowApi {
    public delegate bool EnumChildCallback(IntPtr window, IntPtr parameter);

    [DllImport("user32.dll")]
    public static extern bool EnumChildWindows(IntPtr parent, EnumChildCallback callback, IntPtr parameter);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr window, StringBuilder text, int maxCount);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int SendMessage(IntPtr window, int message, IntPtr wParam, IntPtr lParam);

    public static IntPtr[] ChildWindows(IntPtr parent) {
        var windows = new List<IntPtr>();
        EnumChildWindows(parent, (window, parameter) => {
            windows.Add(window);
            return true;
        }, IntPtr.Zero);
        return windows.ToArray();
    }

    public static string Text(IntPtr window) {
        var text = new StringBuilder(512);
        GetWindowText(window, text, text.Capacity);
        return text.ToString();
    }
}
'@

$xaaRunnerRoot = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
$xaaTestRoot = [IO.Path]::GetFullPath((Join-Path $env:RUNNER_TEMP 'xaa-delete-data-smoke'))
if (-not $xaaTestRoot.StartsWith($xaaRunnerRoot, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $xaaTestRoot)) {
    throw 'Data-removal fixture root must be a new directory under RUNNER_TEMP.'
}
$xaaInstallDir = Join-Path $xaaTestRoot 'app'
if (-not $env:LOCALAPPDATA -or -not $env:APPDATA) { throw 'The hosted runner must provide current-user AppData paths.' }
$xaaLocalAppData = [IO.Path]::GetFullPath($env:LOCALAPPDATA)
$xaaRoamingAppData = [IO.Path]::GetFullPath($env:APPDATA)
$xaaDataDir = Join-Path $xaaLocalAppData 'XAA'
$xaaConfigDir = Join-Path $xaaRoamingAppData 'dev.xyst.xaa'
if ((Test-Path -LiteralPath $xaaDataDir) -or (Test-Path -LiteralPath $xaaConfigDir)) {
    throw 'Refusing to remove pre-existing X.A.A. user data from the hosted runner.'
}
New-Item -ItemType Directory -Path $xaaTestRoot | Out-Null
New-Item -ItemType Directory -Path $xaaInstallDir | Out-Null

$xaaManifest = Get-Content -LiteralPath 'build/installer/manifest.json' -Raw | ConvertFrom-Json
$xaaInstallerName = $xaaManifest.artifacts.installer.filename
if ([IO.Path]::GetFileName($xaaInstallerName) -ne $xaaInstallerName) { throw 'Installer manifest filename escaped its staging directory.' }
$xaaInstaller = (Resolve-Path -LiteralPath (Join-Path 'build/installer' $xaaInstallerName)).Path
if ((Get-FileHash -LiteralPath $xaaInstaller -Algorithm SHA256).Hash -ne $xaaManifest.artifacts.installer.sha256) { throw 'Installer checksum mismatch.' }

$xaaPriorPath = $env:PATH
$env:PATH = Join-Path $env:SystemRoot 'System32'
$xaaReport = @{ passed = $false; explicit_delete_selected = $false; no_model_inference = $true }
$xaaUninstaller = Join-Path $xaaInstallDir 'uninstall.exe'

try {
    $xaaInstall = Start-Process -FilePath $xaaInstaller -ArgumentList @('/S', "/D=$xaaInstallDir") -WindowStyle Hidden -Wait -PassThru
    if ($xaaInstall.ExitCode -ne 0) { throw "Installer exit code $($xaaInstall.ExitCode)" }
    if (-not (Test-Path -LiteralPath $xaaUninstaller)) { throw 'Installer did not create uninstall.exe.' }

    New-Item -ItemType Directory -Path $xaaDataDir, $xaaConfigDir -Force | Out-Null
    $xaaDatabaseMarker = Join-Path $xaaDataDir 'explicit-delete-fixture.sqlite'
    $xaaConfigMarker = Join-Path $xaaConfigDir 'explicit-delete-fixture.txt'
    [IO.File]::WriteAllText($xaaDatabaseMarker, 'Disposable X.A.A. data removal fixture')
    [IO.File]::WriteAllText($xaaConfigMarker, 'Disposable Tauri app-data removal fixture')

    $xaaStartedAt = [DateTime]::UtcNow
    $xaaProcess = Start-Process -FilePath $xaaUninstaller -WindowStyle Normal -WorkingDirectory $xaaInstallDir -PassThru
    $xaaReport.uninstaller_pid = $xaaProcess.Id
    Write-Output "Started interactive uninstaller process $($xaaProcess.Id)."
    $xaaDeadline = [DateTime]::UtcNow.AddSeconds(60)
    $xaaConfirmClicked = $false
    while ([DateTime]::UtcNow -lt $xaaDeadline -and -not $xaaConfirmClicked) {
        $xaaProcess.Refresh()
        if ($xaaProcess.HasExited) {
            $xaaReport.uninstaller_exit_code = $xaaProcess.ExitCode
            $xaaReport.uninstaller_runtime_seconds = [Math]::Round(([DateTime]::UtcNow - $xaaStartedAt).TotalSeconds, 2)
            throw "Uninstaller exited before the data-removal choice (code $($xaaProcess.ExitCode), runtime $($xaaReport.uninstaller_runtime_seconds)s)."
        }
        $xaaWindow = $xaaProcess.MainWindowHandle
        if ($xaaWindow -ne [IntPtr]::Zero) {
            $xaaControls = [XaaInstallerWindowApi]::ChildWindows($xaaWindow)
            $xaaCheckbox = $null
            $xaaUninstallButton = $null
            foreach ($xaaControl in $xaaControls) {
                $xaaText = [XaaInstallerWindowApi]::Text($xaaControl)
                if ($xaaText -eq 'Delete the application data') { $xaaCheckbox = $xaaControl }
                if ($xaaText -eq 'Uninstall') { $xaaUninstallButton = $xaaControl }
            }
            if ($xaaCheckbox -ne $null -and $xaaUninstallButton -ne $null) {
                [void][XaaInstallerWindowApi]::SendMessage($xaaCheckbox, 0x00F1, [IntPtr]::new(1), [IntPtr]::Zero)
                if ([XaaInstallerWindowApi]::SendMessage($xaaCheckbox, 0x00F0, [IntPtr]::Zero, [IntPtr]::Zero) -ne 1) {
                    throw 'Could not check the explicit application-data removal choice.'
                }
                $xaaReport.explicit_delete_selected = $true
                [void][XaaInstallerWindowApi]::SendMessage($xaaUninstallButton, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)
                $xaaConfirmClicked = $true
            }
        }
        if (-not $xaaConfirmClicked) { Start-Sleep -Milliseconds 250 }
    }
    if (-not $xaaConfirmClicked) { throw 'Timed out finding the uninstaller data-removal checkbox and confirmation button.' }
    if (-not $xaaProcess.WaitForExit(120000)) { throw 'Uninstaller did not finish within 120 seconds.' }
    $xaaReport.uninstaller_exit_code = $xaaProcess.ExitCode
    $xaaReport.app_data_removed = -not (Test-Path -LiteralPath $xaaDataDir) -and -not (Test-Path -LiteralPath $xaaConfigDir)
    $xaaReport.binaries_removed = -not (Test-Path -LiteralPath (Join-Path $xaaInstallDir 'xaa-desktop.exe')) -and -not (Test-Path -LiteralPath (Join-Path $xaaInstallDir 'xaa-engine.exe'))
    $xaaReport.passed = $xaaReport.explicit_delete_selected -and $xaaReport.app_data_removed -and $xaaReport.binaries_removed -and $xaaReport.uninstaller_exit_code -eq 0
} finally {
    if (Test-Path -LiteralPath $xaaUninstaller) {
        $xaaCleanup = Start-Process -FilePath $xaaUninstaller -ArgumentList '/S' -WindowStyle Hidden -Wait -PassThru
        if ($xaaCleanup.ExitCode -ne 0) { $xaaReport.cleanup_exit_code = $xaaCleanup.ExitCode }
    }
    $env:PATH = $xaaPriorPath
    $xaaReport | ConvertTo-Json | Set-Content -LiteralPath 'build/installer/delete-data-smoke.json' -Encoding utf8
}
if (-not $xaaReport.passed) { throw 'Explicit application-data removal verification failed.' }
