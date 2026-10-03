[CmdletBinding()]
param([switch]$PostBuild)
$ErrorActionPreference = 'Stop'
$studioRoot = Split-Path -Parent $PSScriptRoot
if ($env:ANOMALY_SDR_RELEASE_BUILD -ne '1' -or $env:GITHUB_ACTIONS -ne 'true') {
    throw 'Public packages are built only on the licensed GitHub Windows Enterprise runner.'
}
$studioVswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$studioEnterprise = & $studioVswhere -latest -products 'Microsoft.VisualStudio.Product.Enterprise' -property installationPath
if (-not $studioEnterprise) { throw 'Licensed Visual Studio Enterprise is required for a release build.' }
$studioVersion = & $studioVswhere -latest -products 'Microsoft.VisualStudio.Product.Enterprise' -property installationVersion
$studioRedistBase = Join-Path $studioEnterprise 'VC\Redist\MSVC'
$studioCandidates = Get-ChildItem -LiteralPath $studioRedistBase -Directory | Where-Object { $_.Name -match '^\d+\.\d+\.\d+$' } | Sort-Object { [Version]$_.Name } -Descending
$studioSelected = $studioCandidates | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'x64\Microsoft.VC143.CRT\vcruntime140.dll') } | Select-Object -First 1
if (-not $studioSelected) { throw 'Enterprise x64 VC143 redistributable runtime was not found.' }
$studioRuntime = Join-Path $studioSelected.FullName 'x64\Microsoft.VC143.CRT'
$env:ANOMALY_SDR_VC_RUNTIME = $studioRuntime
$studioFiles = @()
foreach ($studioName in @('vcruntime140.dll','vcruntime140_1.dll','msvcp140.dll')) {
    $studioSource = Join-Path $studioRuntime $studioName
    if (-not (Test-Path -LiteralPath $studioSource -PathType Leaf)) { throw "Missing official runtime: $studioName" }
    foreach ($studioFolder in @('tools\hackrf\bin','tools\rtl_433')) {
        $studioDestination = Join-Path $studioRoot $studioFolder
        New-Item -ItemType Directory -Path $studioDestination -Force | Out-Null
        Copy-Item -LiteralPath $studioSource -Destination (Join-Path $studioDestination $studioName) -Force
    }
    if ($PostBuild) {
        $studioInternal = Join-Path $studioRoot 'dist\AnomalySDRStudio\_internal'
        if (-not (Test-Path -LiteralPath $studioInternal)) { throw 'Portable application must exist before runtime replacement.' }
        # NumPy imports a hash-suffixed MSVCP filename; keep that imported name
        # while replacing its contents with the unmodified official REDIST.
        $studioRuntimeMatches = Get-ChildItem -LiteralPath $studioInternal -Recurse -File | Where-Object {
            $_.Name -ieq $studioName -or ($studioName -eq 'msvcp140.dll' -and $_.Name -match '^msvcp140-[a-f0-9]+\.dll$')
        }
        $studioRuntimeMatches | ForEach-Object {
            Copy-Item -LiteralPath $studioSource -Destination $_.FullName -Force
        }
    }
    $studioFiles += @{file=$studioName; sha256=(Get-FileHash -LiteralPath $studioSource -Algorithm SHA256).Hash.ToLower();
        file_version=(Get-Item -LiteralPath $studioSource).VersionInfo.FileVersion}
}
$studioProvenance = @{publisher_build_environment='GitHub Actions windows-2022';
    visual_studio_product='Microsoft.VisualStudio.Product.Enterprise';
    visual_studio_version=$studioVersion.Trim();
    source_directory=('VC/Redist/MSVC/' + $studioSelected.Name + '/x64/Microsoft.VC143.CRT');
    unmodified_redist_files=$studioFiles;
    redistribution_basis='Licensed Visual Studio Enterprise build environment and its official 2022 REDIST list';
    redist_reference='https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution';
    runtime_notices='licenses/microsoft-runtime'}
$studioVendor = Join-Path $studioRoot 'vendor'
New-Item -ItemType Directory -Path $studioVendor -Force | Out-Null
$studioProvenance | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $studioVendor 'msvc-runtime-provenance.json') -Encoding utf8
Write-Output 'Official Enterprise redistributable runtime staged and provenance recorded.'
