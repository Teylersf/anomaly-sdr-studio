[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$libraryRoot = Split-Path -Parent $PSScriptRoot
$librarySource = Join-Path $libraryRoot 'vendor\hackrf-guard\src'
$librarySdk = Join-Path $libraryRoot 'tools\hackrf\sdk'
$libraryOutput = Join-Path $libraryRoot 'tools\hackrf\bin\hackrf-0.dll'
$libraryBuild = Join-Path $libraryRoot 'build\hackrf-guard'
New-Item -ItemType Directory -Path $libraryBuild -Force | Out-Null
$libraryVswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$libraryProducts = if ($env:ANOMALY_SDR_RELEASE_BUILD -eq '1') { 'Microsoft.VisualStudio.Product.Enterprise' } else { '*' }
$libraryVs = & $libraryVswhere -latest -products $libraryProducts -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $libraryVs) { throw 'Visual Studio C++ x64 Build Tools are required.' }
$libraryVcvars = Join-Path $libraryVs 'VC\Auxiliary\Build\vcvars64.bat'
$libraryCommand = 'call "{0}" >nul && cl.exe /nologo /LD /O2 /W3 /MD /GS /sdl /D_CRT_SECURE_NO_WARNINGS /DWINPTHREADS_USE_DLLIMPORT /FI"{1}\version-config.h" /I"{1}" /I"{2}\include" /I"{2}\include\libusb-1.0" "{1}\hackrf.c" /Fo"{3}\hackrf.obj" /Fe"{4}" /link /MACHINE:X64 /IMPLIB:"{3}\hackrf.lib" "{2}\lib\libusb-1.0.lib" "{2}\lib\pthread.lib"' -f $libraryVcvars,$librarySource,$librarySdk,$libraryBuild,$libraryOutput
& $env:ComSpec /d /s /c $libraryCommand
if ($LASTEXITCODE -ne 0) { throw 'Guarded HackRF library compilation failed.' }
