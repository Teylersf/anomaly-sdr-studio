[CmdletBinding()]
param([switch]$TestFixture)

$ErrorActionPreference = 'Stop'
$taskSource = Join-Path $PSScriptRoot 'radio_control_native.c'
$taskExecutable = Join-Path $PSScriptRoot 'radio_control_native.exe'
if ($TestFixture) {
    $taskSource = Join-Path $PSScriptRoot 'testdata\mock_radio.c'
    $taskExecutable = Join-Path $PSScriptRoot 'testdata\mock-hackrf.dll'
}
$taskVswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $taskSource -PathType Leaf)) {
    throw "Native source is missing: $taskSource"
}
if (-not (Test-Path -LiteralPath $taskVswhere -PathType Leaf)) {
    throw "Visual Studio installation locator is missing: $taskVswhere"
}
$taskProducts = if ($env:ANOMALY_SDR_RELEASE_BUILD -eq '1') { 'Microsoft.VisualStudio.Product.Enterprise' } else { '*' }
$taskVisualStudio = & $taskVswhere -latest -products $taskProducts -version '[17.0,18.0)' `
    -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ($LASTEXITCODE -ne 0 -or -not $taskVisualStudio) {
    throw 'Visual Studio 2022 x64 C++ Build Tools were not found.'
}
$taskVcvars = Join-Path ([string]$taskVisualStudio) 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path -LiteralPath $taskVcvars -PathType Leaf)) {
    throw "The x64 compiler environment script is missing: $taskVcvars"
}
$taskObject = Join-Path ([IO.Path]::GetTempPath()) ('anomaly-radio-control-' + [guid]::NewGuid().ToString('N') + '.obj')
$taskImportLibrary = [IO.Path]::ChangeExtension($taskObject, '.lib')
$taskExportFile = [IO.Path]::ChangeExtension($taskObject, '.exp')
# One cmd.exe instance owns vcvars64 and cl's compiler environment. No file
# operations are delegated to cmd.exe; the only temporary file is ours.
$taskBuildCommand = 'call "{0}" >nul && cl.exe /nologo /O2 /W4 /MT /DUNICODE /D_UNICODE "{1}" /Fe:"{2}" /Fo:"{3}" /link /INCREMENTAL:NO' -f `
    $taskVcvars, $taskSource, $taskExecutable, $taskObject
if ($TestFixture) {
    $taskBuildCommand = 'call "{0}" >nul && cl.exe /nologo /O2 /W4 /MT /LD "{1}" /Fe:"{2}" /Fo:"{3}" /link /INCREMENTAL:NO /IMPLIB:"{4}"' -f `
        $taskVcvars, $taskSource, $taskExecutable, $taskObject, $taskImportLibrary
}
try {
    Push-Location -LiteralPath $PSScriptRoot
    try {
        & $env:ComSpec /d /s /c $taskBuildCommand
        if ($LASTEXITCODE -ne 0) {
            throw "Native helper compilation failed with exit code $LASTEXITCODE."
        }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path -LiteralPath $taskExecutable -PathType Leaf)) {
        throw 'Compiler reported success without producing the native helper.'
    }
    Write-Output "Built $taskExecutable using Visual Studio 2022 x64 with /MT."
} finally {
    foreach ($taskTemporaryFile in @($taskObject, $taskImportLibrary, $taskExportFile)) {
        if (Test-Path -LiteralPath $taskTemporaryFile -PathType Leaf) {
            Remove-Item -LiteralPath $taskTemporaryFile -Force
        }
    }
}
