# Build Anomaly SDR Studio

The Windows release is a portable, one-folder application. Extract its complete ZIP and run a launcher. It includes the Python runtime, NumPy, the local UI, native HackRF host tools, a checked radio-control helper, and the optional file-only rtl_433 decoder. The app and build scripts do not install a USB driver or change device firmware.

## Build requirements

- Windows 10/11 x64.
- Python 3.13 x64 for the documented build.
- Visual Studio 2022 C++ Build Tools with the x64 compiler and Windows SDK.
- Internet access to the pinned Python-package, conda-forge, and official upstream downloads.

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r build-requirements.txt
.\Build-Windows.ps1
```

The local build prepares native tools, rebuilds the guarded host library and custom control helper, runs offline tests, and creates the development app under `dist/AnomalySDRStudio` using `anomaly-sdr-studio.spec`. It does not create public release ZIPs. It performs no RF transmission or hardware receive test.

The official GitHub Enterprise build uses `Build-Windows.ps1 -ReleaseBuild` and produces:

- `release/anomaly-sdr-studio-v0.1.0-windows-x64.zip`
- `release/anomaly-sdr-studio-v0.1.0-source.zip`
- `release/SHA256SUMS.txt`

The application folder is `AnomalySDRStudio`, containing `AnomalySDRStudio.exe`, `StartAnomalySDRStudio.cmd`, `StartDemo.cmd`, `StartOffline.cmd`, and `Start-AnomalySDRStudio.ps1`. The `.cmd` launchers start the service in the background and open the local browser. The console executable can also run directly. **Quit app** stops reception and closes the service after checked cleanup.

## Pinned dependencies and source

`requirements.txt` pins NumPy 2.2.1. `build-requirements.txt` pins the packaging dependencies, including PyInstaller 6.22.3 and zstandard 0.25.0. The release manifest records the runtime and native files actually packaged.

`scripts/prepare_native_tools.py` verifies SHA-256 hashes of pinned native downloads. Needed host tools and runtime DLLs go under `tools/hackrf/bin`, build headers and import libraries under `tools/hackrf/sdk`, original recipes and patches under `vendor/recipes`, and license texts under `licenses`. Firmware-writing tools are excluded from the app.

Native components include HackRF 2024.02.1, libusb 1.0.30, winpthreads from the pinned mingw-w64 package, and the Microsoft Visual C++ runtime. rtl_433 25.12 uses its pinned file-only Windows build under `tools/rtl_433`. Exact URLs, hashes, and build provenance accompany the source distribution.

The source ZIP includes the application, build scripts, license notices, full upstream source archives under `vendor/source-archives`, and the project's guarded libhackrf sources and patch under `vendor/hackrf-guard`. It includes corresponding source and recipes for the distributed HackRF tools, libusb, rtl_433, and winpthreads. Keep that source ZIP available alongside the Windows ZIP when redistributing the bundled native programs. See [Third-party notices](../THIRD_PARTY_NOTICES.md).

## Microsoft runtime selection for public releases

Public Windows packages are built on the GitHub `windows-2022` runner with its Visual Studio Enterprise 2022 installation. The release build uses `Build-Windows.ps1 -ReleaseBuild` and `scripts/Prepare-VisualStudioRuntime.ps1` to select unmodified, non-debug CRT DLLs from that installation's REDIST directory and replace extra copies before packaging. `vendor/msvc-runtime-provenance.json` records the actual runtime versions and hashes. Local preview builds do not qualify automatically as public release packages.

Microsoft runtime redistribution depends on a valid Visual Studio license and its **Distributable Code** terms; the runtime's end-user license alone does not grant redistribution rights. Original documents and the REDIST reference are supplied under `licenses/microsoft-runtime`. Keep Microsoft components separately licensed. [Visual Studio 2022 distributable list](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution), [Enterprise/Professional terms](https://visualstudio.microsoft.com/license-terms/vs2022-ga-proenterprise/), [GitHub Windows image software](https://github.com/actions/runner-images/blob/main/images/windows/Windows2022-Readme.md).

## Guarded libhackrf and control helper

The libhackrf source starts from upstream commit `18b485e3b6d2031c15a79ba89cdb42b5fa245f24`. The serial-descriptor guard patch preserves signed USB errors and rejects failed, oversized, or undersized descriptors before indexing or comparing serial buffers. It does not change RF settings, firmware, or modulation. `scripts/Build-HackRFLibrary.ps1` builds this DLL with the pinned libusb and winpthreads SDK.

The original `radio_control_native.c` helper dynamically loads the chosen host library, selects an unambiguous HackRF One, and checks device open, mode-off, close, and exit results. Build it with:

```powershell
.\Build-RadioControl.ps1
.\Build-RadioControl.ps1 -TestFixture
```

The test fixture is an offline DLL and is excluded from the Windows package. Compiler versions, timestamps, and packaging environments can change binary hashes; this is a documented source build, not a bit-for-bit reproducibility guarantee.

## Offline verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s . -p 'test_*.py'
.\.venv\Scripts\python.exe launch.py --offline --demo --no-browser --port 8790 --data-dir .\build\preview-state
```

Open `http://127.0.0.1:8790/` to inspect the explicitly labeled synthetic demo. It performs no USB access. Check the second analysis workspace, plots, format and decryption workbench, and **Quit app**. Cryptographic checks use public test vectors and locally generated test data, rather than radio traffic.

The GitHub Windows workflow runs offline checks. It does not have a radio and cannot verify an operator's USB driver, antenna, receiver calibration, or real-world RF decoding.
