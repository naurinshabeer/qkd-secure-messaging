$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProjectPython = [IO.Path]::GetFullPath((Join-Path $ProjectRoot '.venv\Scripts\python.exe'))

$NetworkProcesses = Get-CimInstance Win32_Process | Where-Object {
    $CommandLine = [string]$_.CommandLine
    $Executable = [string]$_.ExecutablePath
    $IsProjectPython = $Executable -and [string]::Equals(
        [IO.Path]::GetFullPath($Executable), $ProjectPython, [StringComparison]::OrdinalIgnoreCase)
    $IsNetworkModule = $CommandLine -match '(?i)(?:^|\s)-m\s+qkd_demo\.(?:coordinator|node)(?:\s|$)'
    $IsProjectPython -and $IsNetworkModule
}

if (-not $NetworkProcesses) {
    Write-Host 'No coordinator or node processes from this project were found.'
    return
}

foreach ($Process in $NetworkProcesses) {
    Stop-Process -Id $Process.ProcessId -Force
    Write-Host "Stopped project network process $($Process.ProcessId): $($Process.CommandLine)"
}
