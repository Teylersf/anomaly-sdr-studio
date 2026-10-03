# Third-party notices

Anomaly SDR Studio's original Python code, browser UI, documentation, and custom native control helper are licensed under the [MIT License](LICENSE). Bundled tools, libraries, and runtimes keep their original copyright notices and licenses. The application's MIT license does not relicense those components.

## HackRF host tools

The distributed `hackrf_transfer` and `hackrf_info` binaries come from **HackRF 2024.02.1**. Their source is licensed under **GPL-2.0-or-later**, with copyright notices for Great Scott Gadgets, Jared Boone, Benjamin Vernoux, and other contributors. Original source headers and the GNU license text are preserved in the supplied source and license files.

- [Pinned HackRF release](https://github.com/greatscottgadgets/hackrf/releases/tag/v2024.02.1)
- [Transfer source and license header](https://github.com/greatscottgadgets/hackrf/blob/v2024.02.1/host/hackrf-tools/src/hackrf_transfer.c)
- Upstream commit: `18b485e3b6d2031c15a79ba89cdb42b5fa245f24`

The app starts these programs as separate child processes. Its Windows release includes their corresponding source and native build information in the matching source ZIP.

## libhackrf

`hackrf-0.dll` is a project-local rebuild of **libhackrf 2024.02.1**, licensed under **BSD-3-Clause**. The original library notices for Great Scott Gadgets, Jared Boone, and Benjamin Vernoux remain in its source.

The local patch guards USB serial-descriptor reads against failed, oversized, or too-short results before buffer access. It changes no radio firmware or modulation. Original source, patched source, the patch, and build instructions accompany the release. This local build is not an official upstream release or endorsement. [Original library source](https://github.com/greatscottgadgets/hackrf/blob/v2024.02.1/host/libhackrf/src/hackrf.c).

## rtl_433

The optional file-only decoder binary is **rtl_433 25.12**, licensed under **GPL-2.0-or-later**. Its original program headers credit Benjamin Larsson and Steve Markgraf; individual decoders and other source files retain their additional author notices. The distributed source archive includes the complete upstream source and its GNU license text.

- [Official release](https://github.com/merbanan/rtl_433/releases/tag/25.12)
- [Program source and license header](https://github.com/merbanan/rtl_433/blob/25.12/src/rtl_433.c)
- Bundled license: `licenses/rtl_433/COPYING`

The app feeds finite local files to this decoder. The executable remains a separately licensed third-party program; supported-protocol recognition is not a claim of upstream endorsement.

## Native libraries and runtimes

| Component | License or terms | Included notices |
| --- | --- | --- |
| libusb 1.0.30 | LGPL-2.1-or-later | `licenses/libusb/COPYING`, original source headers, corresponding source and recipe |
| libwinpthread / winpthreads | MIT with incorporated BSD notices | `licenses/libwinpthread` and `licenses/winpthreads-devel`, including mingw-w64 and Lockless notices |
| Microsoft Visual C++ runtime | Microsoft redistribution terms | Original terms and REDIST reference under `licenses/microsoft-runtime`; Conda recipe notices remain under `licenses/vc14_runtime` |

libusb is dynamically loaded through the native library; its corresponding source is supplied. Original component licenses and build recipes remain available in the source ZIP. System-provided Windows components are not relicensed by this app.

[libusb source](https://github.com/libusb/libusb/tree/v1.0.30), [winpthreads license](https://github.com/mingw-w64/mingw-w64/blob/master/mingw-w64-libraries/winpthreads/COPYING), [Microsoft runtime redistribution guidance](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170).

The public release selects unmodified Microsoft CRT DLLs from the licensed Visual Studio Enterprise 2022 REDIST installation used by the GitHub Windows build. Their selected versions and hashes appear in `vendor/msvc-runtime-provenance.json`. The original Visual Studio distribution terms and runtime end-user agreement are preserved. The runtime end-user agreement alone is not the publisher's distribution grant; the Visual Studio **Distributable Code** terms and REDIST list govern redistribution. See [Microsoft runtime notices](licenses/microsoft-runtime/README.md).

## Python, NumPy, and packaging

| Component | License | Included notices |
| --- | --- | --- |
| Python 3.13.7 runtime and incorporated software | PSF and original component licenses | Full `licenses/python/LICENSE.txt`, incorporated-software notice, and separate OpenSSL/Expat/HACL notices |
| NumPy 2.2.1 | BSD-3-Clause with bundled dependency notices | Full installed-wheel notice in `licenses/numpy/LICENSE.txt` |
| PyInstaller bootloader | GPL-2.0-or-later with its bootloader exception | `licenses/pyinstaller/LICENSE.txt` |

The Python notices preserve original licenses for its incorporated software. OpenSSL 3.0.16 uses Apache-2.0; Expat and HACL* keep their original MIT notices. The full installed Windows Python license also preserves libffi and bzip2 terms, while its documentation notice covers zlib, libmpdec, mimalloc, and other incorporated code. These are runtime dependency notices, not features advertised by the app. [Python incorporated-software licenses](https://github.com/python/cpython/blob/v3.13.7/Doc/license.rst).

The NumPy wheel's full notice also covers its bundled OpenBLAS, LAPACK, and GCC runtime components, including the relevant GCC runtime exception. Preserve that complete file rather than only NumPy's short BSD notice.

The PyInstaller exception permits distributing the packaged application under its own license; it does not replace the licenses of components bundled inside it. zstandard is a build-time dependency for extracting pinned package archives and is not an application runtime dependency.

[Python license](https://docs.python.org/3.13/license.html), [NumPy license](https://numpy.org/doc/2.2/license.html), [PyInstaller 6.22.3 license and exception](https://pyinstaller.org/en/v6.22.3/license.html).

## Source and redistribution

Release assets include `anomaly-sdr-studio-v0.1.0-windows-x64.zip`, `anomaly-sdr-studio-v0.1.0-source.zip`, and `SHA256SUMS.txt`. The source ZIP includes application source, full pinned upstream archives, guarded library source, patches, recipes, license files, and build instructions. The manifests identify the actual packaged dependency versions and files.

Redistributors must preserve the relevant copyright and license notices and the corresponding source supplied for bundled GPL/LGPL programs. HackRF One and Great Scott Gadgets identify the supported hardware and upstream project. Third-party names do not imply affiliation or endorsement.
