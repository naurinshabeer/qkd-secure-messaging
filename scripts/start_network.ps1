[CmdletBinding()]
param(
    [string]$Topology = 'config\topology.json',
    [string]$Token = $env:QKD_PROVISION_TOKEN
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if ([string]::IsNullOrWhiteSpace($Token)) {
    throw 'Pass -Token or set QKD_PROVISION_TOKEN before starting the network.'
}
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project Python was not found at $Python. Create the virtual environment and install requirements first."
}
$TopologyPath = (Resolve-Path -LiteralPath $Topology).Path
$TopologyObject = Get-Content -LiteralPath $TopologyPath -Raw | ConvertFrom-Json
if (-not $TopologyObject.nodes -or -not $TopologyObject.links) {
    throw "Topology file must contain nodes and links: $TopologyPath"
}
$NodeProperties = @($TopologyObject.nodes.PSObject.Properties)

function Quote-PowerShell([string]$Value) {
    return "'" + $Value.Replace("'", "''") + "'"
}

function Start-ProjectWindow([string]$Command) {
    $Encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Command))
    $Engine = (Get-Process -Id $PID).Path
    Start-Process -FilePath $Engine -ArgumentList @('-NoExit', '-EncodedCommand', $Encoded) | Out-Null
}

$RootArg = Quote-PowerShell $ProjectRoot
$PythonArg = Quote-PowerShell $Python
$TopologyArg = Quote-PowerShell $TopologyPath
$TokenArg = Quote-PowerShell $Token
$Prefix = "Set-Location -LiteralPath $RootArg; `$env:QKD_PROVISION_TOKEN = $TokenArg; & $PythonArg -m "

$CoordinatorCommand = $Prefix + "qkd_demo.coordinator --host 127.0.0.1 --port 8765 --topology $TopologyArg"
Start-ProjectWindow $CoordinatorCommand
Start-Sleep -Milliseconds 800

foreach ($Property in $NodeProperties) {
    $NodeId = [string]$Property.Name
    $Role = [string]$Property.Value.role
    $NodeIdArg = Quote-PowerShell $NodeId
    $RoleArg = Quote-PowerShell $Role
    $NodeCommand = $Prefix + "qkd_demo.node --role $RoleArg --node-id $NodeIdArg --topology $TopologyArg --coordinator 'http://127.0.0.1:8765'"
    Start-ProjectWindow $NodeCommand
    Start-Sleep -Milliseconds 250
}

Write-Host "Started coordinator and $($NodeProperties.Count) nodes in separate PowerShell windows."
Write-Host 'Dashboard: http://127.0.0.1:8765'
