"""Package the portable application plus complete corresponding source."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_config import APP_NAME, VERSION


def digest(path):
    with path.open('rb') as source:
        checksum = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b''):
            checksum.update(block)
        return checksum.hexdigest()


def add_tree(archive, directory, prefix):
    for path in sorted(directory.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            archive.write(path, (Path(prefix) / path.relative_to(directory)).as_posix())


def canonical_runtime_name(filename):
    """Preserve NumPy's imported alias while validating the underlying DLL."""
    name = filename.lower()
    if name in {'vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll'}:
        return name
    if re.fullmatch(r'msvcp140-[a-f0-9]+\.dll', name):
        return 'msvcp140.dll'
    return None


def public_build_provenance():
    commit = os.environ.get('GITHUB_SHA', '')
    run_id = os.environ.get('GITHUB_RUN_ID', '')
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    if (not re.fullmatch(r'[a-fA-F0-9]{40,64}', commit)
            or not run_id.isdigit()
            or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)
            or os.environ.get('GITHUB_SERVER_URL') != 'https://github.com'):
        raise RuntimeError('Public GitHub commit and build-run provenance are required.')
    return {'source_commit': commit.lower(), 'repository': repository,
            'build_run_id': run_id,
            'build_run_url': f'https://github.com/{repository}/actions/runs/{run_id}'}


def main():
    if os.environ.get('ANOMALY_SDR_RELEASE_BUILD') != '1' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('Public release packaging requires the licensed GitHub Windows Enterprise build.')
    build_provenance = public_build_provenance()
    runtime_provenance_path = ROOT / 'vendor' / 'msvc-runtime-provenance.json'
    runtime_provenance = json.loads(runtime_provenance_path.read_text(encoding='utf-8-sig'))
    if runtime_provenance.get('visual_studio_product') != 'Microsoft.VisualStudio.Product.Enterprise':
        raise RuntimeError('Runtime provenance does not identify a licensed Enterprise build.')
    output = ROOT / 'dist' / 'AnomalySDRStudio'
    if not (output / 'AnomalySDRStudio.exe').is_file():
        raise RuntimeError('Build the standalone executable before packaging.')
    licenses = ROOT / 'licenses'
    for package, filename, label in [('numpy', 'LICENSE.txt', 'numpy'),
                                     ('pyinstaller', 'licenses/COPYING.txt', 'pyinstaller')]:
        distribution = importlib.metadata.distribution(package)
        license_file = Path(distribution._path) / filename
        if not license_file.is_file():
            raise RuntimeError(f'Missing license for {package}.')
        target = licenses / label
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(license_file, target / 'LICENSE.txt')
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    (licenses / 'python').mkdir(parents=True, exist_ok=True)
    shutil.copyfile(python_license, licenses / 'python' / 'LICENSE.txt')
    launchers = ['StartAnomalySDRStudio.cmd', 'StartDemo.cmd', 'StartOffline.cmd',
                 'Start-AnomalySDRStudio.ps1']
    notices = ['README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md']
    for filename in launchers + notices:
        shutil.copyfile(ROOT / filename, output / filename)
    shutil.copytree(licenses, output / 'licenses', dirs_exist_ok=True)
    shutil.copytree(ROOT / 'docs', output / 'docs', dirs_exist_ok=True)
    for source in ['hackrf-2024.02.1.tar.xz', 'libusb-1.0.30.tar.bz2',
                   'rtl_433-25.12.tar.gz',
                   'mingw-w64-dc42231f0392f75de72e87ba0170ec60fcc6c10b.tar.gz']:
        if not (ROOT / 'vendor' / 'source-archives' / source).is_file():
            raise RuntimeError('Complete corresponding source archive is missing: ' + source)
    source_name = f'anomaly-sdr-studio-v{VERSION}-source.zip'
    manifest = {'application': APP_NAME, 'version': VERSION, 'platform': 'Windows x64',
                'python': sys.version.split()[0], 'numpy': importlib.metadata.version('numpy'),
                'pyinstaller': importlib.metadata.version('pyinstaller'),
                'radio_mode': 'receive only; no transmit path',
                'runtime_distribution': runtime_provenance,
                **build_provenance,
                'application_executable_sha256': digest(output / 'AnomalySDRStudio.exe'),
                'native_files': {}, 'corresponding_source': source_name}
    for path in sorted((output / '_internal').rglob('*')):
        if path.is_file() and path.suffix.lower() in ('.dll', '.exe'):
            manifest['native_files'][path.relative_to(output).as_posix()] = digest(path)
        if path.is_file() and (path.name.lower() == 'ucrtbase.dll'
                              or path.name.lower().startswith(('api-ms-win-', 'ext-ms-win-'))):
            raise RuntimeError('Windows operating-system runtime must not be bundled: ' + path.name)
        runtime_name = canonical_runtime_name(path.name) if path.is_file() else None
        if runtime_name:
            expected = next((item['sha256'] for item in runtime_provenance['unmodified_redist_files']
                             if item['file'].lower() == runtime_name), None)
            if expected is None or digest(path) != expected:
                raise RuntimeError('Packaged runtime differs from official Enterprise REDIST: ' + path.name)
    (output / 'release-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    release = ROOT / 'release'
    release.mkdir(exist_ok=True)
    binary_zip = release / f'anomaly-sdr-studio-v{VERSION}-windows-x64.zip'
    with zipfile.ZipFile(binary_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        add_tree(archive, output, 'AnomalySDRStudio')
    source_zip = release / source_name
    with zipfile.ZipFile(source_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(output / 'release-manifest.json', 'anomaly-sdr-studio/release-manifest.json')
        # Explicit source extensions prevent keys, IQ, logs, environments and
        # generated DLL/EXE fixtures from entering the corresponding-source ZIP.
        for path in sorted(ROOT.iterdir()):
            if path.is_file() and (path.suffix in {'.py', '.c', '.h', '.cjs', '.js', '.html', '.ps1', '.cmd', '.spec'}
                                   or path.name in notices + ['requirements.txt', 'build-requirements.txt', '.gitignore']):
                archive.write(path, 'anomaly-sdr-studio/' + path.name)
        for folder in ['vendor', 'licenses', 'scripts', 'docs', '.github']:
            add_tree(archive, ROOT / folder, 'anomaly-sdr-studio/' + folder)
        for path in sorted((ROOT / 'testdata').glob('*.c')):
            archive.write(path, 'anomaly-sdr-studio/testdata/' + path.name)
    checksum_lines = [digest(path) + '  ' + path.name for path in [binary_zip, source_zip]]
    (release / 'SHA256SUMS.txt').write_text('\n'.join(checksum_lines) + '\n', encoding='ascii')
    print('\n'.join(checksum_lines))


if __name__ == '__main__':
    main()
