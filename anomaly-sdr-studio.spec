# Run with .venv/Scripts/python.exe -m PyInstaller anomaly-sdr-studio.spec.
from pathlib import Path
root = Path(SPECPATH)
data = [(str(root / 'index.html'), '.'), (str(root / 'crypto-workbench.js'), '.')]
binary_files = [(str(root / 'radio_control_native.exe'), '.')]
binary_files += [(str(path), 'tools/hackrf/bin')
                 for path in (root / 'tools/hackrf/bin').iterdir() if path.is_file()]
binary_files += [(str(root / 'tools/rtl_433' / name), 'tools/rtl_433')
                 for name in ['rtl_433.exe', 'vcruntime140.dll']]
a = Analysis([str(root / 'launch.py')], pathex=[str(root)], binaries=binary_files,
             datas=data, hiddenimports=['lab_server', 'radio_control'], hookspath=[],
             runtime_hooks=[], excludes=['tkinter'], noarchive=False)
# The supported Windows 10/11 operating system supplies UCRT and API-set DLLs.
# Do not redistribute SDK/Conda copies discovered while resolving imports.
def operating_system_runtime(entry):
    name = Path(entry[0]).name.lower()
    return name == 'ucrtbase.dll' or name.startswith(('api-ms-win-', 'ext-ms-win-'))
a.binaries = [entry for entry in a.binaries if not operating_system_runtime(entry)]
a.datas = [entry for entry in a.datas if not operating_system_runtime(entry)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AnomalySDRStudio',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='AnomalySDRStudio')
