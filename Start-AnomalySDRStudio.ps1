[CmdletBinding()]
param([switch]$Demo, [switch]$Offline)
$ErrorActionPreference = 'Stop'
$studioExecutable = Join-Path $PSScriptRoot 'AnomalySDRStudio.exe'
$studioArguments = @()
if (-not (Test-Path -LiteralPath $studioExecutable -PathType Leaf)) {
    $studioExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $studioExecutable -PathType Leaf)) {
        $studioExecutable = (Get-Command python.exe -ErrorAction Stop).Source
    }
    $studioArguments += ('"' + (Join-Path $PSScriptRoot 'launch.py') + '"')
}
if ($Demo) { $studioArguments += '--demo' }
if ($Offline -or $Demo) { $studioArguments += '--offline' }
$studioState = Join-Path $env:LOCALAPPDATA 'AnomalySDRStudio'
New-Item -ItemType Directory -Path $studioState -Force | Out-Null
$studioStart = @{FilePath=$studioExecutable; WorkingDirectory=$PSScriptRoot;
    WindowStyle='Hidden'; PassThru=$true;
    RedirectStandardOutput=(Join-Path $studioState 'launcher.stdout.log');
    RedirectStandardError=(Join-Path $studioState 'launcher.stderr.log')}
if ($studioArguments.Count) { $studioStart.ArgumentList = $studioArguments }
$studioProcess = Start-Process @studioStart
Start-Sleep -Milliseconds 750
if ($studioProcess.HasExited -and $studioProcess.ExitCode -ne 0) {
    $studioFailure = Get-Content -Raw -LiteralPath (Join-Path $studioState 'launcher.stderr.log')
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($studioFailure, 'Anomaly SDR Studio could not start') | Out-Null
}
