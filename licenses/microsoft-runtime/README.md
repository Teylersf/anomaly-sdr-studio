# Microsoft runtime notices

The Windows package contains unmodified Microsoft Visual C++ runtime DLLs. They are proprietary Microsoft components and are not covered by Anomaly SDR Studio's MIT license.

Public release builds select the release CRT files from the licensed Visual Studio Enterprise 2022 installation on the GitHub Windows build runner. `vendor/msvc-runtime-provenance.json` records the chosen runtime versions and SHA-256 hashes. The release builder replaces additional runtime copies with the selected files before packaging.

The publisher's distribution rights come from the Visual Studio license's **Distributable Code** section and its referenced REDIST list. The runtime end-user license governs recipients' use; it is not by itself a redistribution grant. Preserve both the applicable terms and the original Microsoft notices. A local installation of Build Tools alone must not be treated as proof of a Visual Studio distribution license.

Included documents:

- `Visual-Studio-2022-Enterprise-Professional-License.docx`: Microsoft's original license document.
- `Visual-Studio-2022-Enterprise-Professional-License.txt`: convenience text extraction; the original document is authoritative.
- `REDIST.txt`: the installed Visual Studio reference to the current distributable list.
- `Runtime-End-User-License.docx`: Microsoft's original runtime end-user agreement.
- `Runtime-End-User-License.txt`: convenience text extraction; the original document is authoritative.

Official references:

- [Enterprise/Professional 2022 license](https://visualstudio.microsoft.com/license-terms/vs2022-ga-proenterprise/)
- [Original license document](https://visualstudio.microsoft.com/wp-content/uploads/2021/11/Visual-Studio-2022-Enterprise-Professional-License-EN.docx)
- [Visual Studio 2022 distributable list](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution)
- [Runtime end-user license](https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/)
- [Visual C++ redistribution guidance](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170)

These copies identify the applicable documents; including them does not create distribution rights for an otherwise unlicensed publisher. Redistributors must follow the original terms, including restrictions on modification, previews, attribution, and protections for the Microsoft components.
