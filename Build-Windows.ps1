[CmdletBinding()]
param([switch]$ReleaseBuild)
$ErrorActionPreference = 'Stop'
$studioPreviousReleaseBuild = $env:ANOMALY_SDR_RELEASE_BUILD
$studioPreviousRuntime = $env:ANOMALY_SDR_VC_RUNTIME
Push-Location -LiteralPath $PSScriptRoot
try {
    $env:ANOMALY_SDR_RELEASE_BUILD = if ($ReleaseBuild) { '1' } else { $null }
    $studioPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $studioPython)) {
        & python.exe -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the build environment.' }
    }
    & $studioPython -m pip install -r build-requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Build dependencies could not be installed.' }
    if ($ReleaseBuild) {
        & (Join-Path $PSScriptRoot 'scripts\Prepare-VisualStudioRuntime.ps1')
    }
    & $studioPython scripts\prepare_native_tools.py
    if ($LASTEXITCODE -ne 0) { throw 'Native dependencies could not be prepared.' }
    & (Join-Path $PSScriptRoot 'scripts\Build-HackRFLibrary.ps1')
    & (Join-Path $PSScriptRoot 'Build-RadioControl.ps1')
    & (Join-Path $PSScriptRoot 'Build-RadioControl.ps1') -TestFixture
    & $studioPython -m unittest discover -s . -p 'test_*.py'
    if ($LASTEXITCODE -ne 0) { throw 'Offline checks failed.' }
    & node.exe test_ui.cjs
    if ($LASTEXITCODE -ne 0) { throw 'UI checks failed.' }
    & node.exe test_crypto.cjs
    if ($LASTEXITCODE -ne 0) { throw 'Crypto checks failed.' }
    & $studioPython -m PyInstaller --noconfirm anomaly-sdr-studio.spec
    if ($LASTEXITCODE -ne 0) { throw 'Standalone build failed.' }
    if ($ReleaseBuild) {
        & (Join-Path $PSScriptRoot 'scripts\Prepare-VisualStudioRuntime.ps1') -PostBuild
        & $studioPython scripts\make_release.py
        if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed.' }
    } else {
        Write-Output 'Development build complete. Public release packages require the licensed Enterprise CI build.'
    }
} finally {
    $env:ANOMALY_SDR_RELEASE_BUILD = $studioPreviousReleaseBuild
    $env:ANOMALY_SDR_VC_RUNTIME = $studioPreviousRuntime
    Pop-Location
}
