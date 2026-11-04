param([Parameter(Mandatory = $true)][int]$DesktopProcessId)
$ErrorActionPreference = 'Stop'
# Project only process identity and debugging flags; never emit raw command lines.
$xaaProcesses = @(Get-CimInstance Win32_Process)
$xaaOwned = [Collections.Generic.HashSet[int]]::new()
[void]$xaaOwned.Add($DesktopProcessId)
do {
    $xaaAdded = $false
    foreach ($xaaProcess in $xaaProcesses) {
        if ($xaaOwned.Contains([int]$xaaProcess.ParentProcessId)) {
            if ($xaaOwned.Add([int]$xaaProcess.ProcessId)) { $xaaAdded = $true }
        }
    }
} while ($xaaAdded)
$xaaChildren = @($xaaProcesses | Where-Object { $xaaOwned.Contains([int]$_.ProcessId) } | ForEach-Object {
    $xaaCommand = [string]$_.CommandLine
    $xaaDebugPort = $null
    if ($xaaCommand -match '--remote-debugging-port[= ](\d+)') { $xaaDebugPort = $Matches[1] }
    [pscustomobject]@{
        name = $_.Name
        pid = $_.ProcessId
        parent_pid = $_.ParentProcessId
        debugging_port = $xaaDebugPort
        has_user_data_dir_flag = $xaaCommand.Contains('--user-data-dir')
        runtime_version = if ($_.Name -eq 'msedgewebview2.exe' -and $_.ExecutablePath) { [Diagnostics.FileVersionInfo]::GetVersionInfo($_.ExecutablePath).FileVersion } else { $null }
    }
})
$xaaPolicies = @()
foreach ($xaaPolicyPath in @('HKLM:\Software\Policies\Microsoft\Edge', 'HKCU:\Software\Policies\Microsoft\Edge', 'HKLM:\Software\Policies\Microsoft\Edge\WebView2', 'HKCU:\Software\Policies\Microsoft\Edge\WebView2')) {
    $xaaValue = (Get-ItemProperty -LiteralPath $xaaPolicyPath -ErrorAction SilentlyContinue).RemoteDebuggingAllowed
    if ($null -ne $xaaValue) { $xaaPolicies += @{ path = $xaaPolicyPath; remote_debugging_allowed = $xaaValue } }
}
$xaaListeners = @(Get-NetTCPConnection -State Listen -LocalPort 19222 -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort, OwningProcess)
@{ processes = $xaaChildren; policies = $xaaPolicies; debugging_listeners = $xaaListeners } | ConvertTo-Json -Depth 5 -Compress
