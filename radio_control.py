"""Checked JSON wrapper for the native, target-specific HackRF control helper.

Python never loads the radio DLL. Info and idle requests run in a separate native
executable, which checks open, close, and exit before reporting success.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
from app_config import ASSET_ROOT, tools_directory, device_serial

ROOT = ASSET_ROOT
BIN = tools_directory()
SERIAL = None


def run_control(idle=False, serial=None, tools_dir=None):
    selected = device_serial(serial if serial is not None else SERIAL)
    tool_bin = tools_directory(tools_dir) if tools_dir else BIN
    command = [str(ROOT / 'radio_control_native.exe'), '--dll', str(tool_bin / 'hackrf-0.dll')]
    if selected:
        command.extend(['--serial', selected])
    if idle and not selected:
        raise ValueError('Checked mode OFF requires an explicitly selected serial.')
    if idle:
        command.append('--idle')
    environment = os.environ.copy()
    environment['PATH'] = str(tool_bin) + os.pathsep + environment.get('PATH', '')
    result = subprocess.run(command, capture_output=True, timeout=8, env=environment,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    receipt = result.stderr.decode('utf-8', 'replace')
    if result.returncode != 0:
        detail = (result.stdout.decode('utf-8', 'replace') + '\n' + receipt)[-1600:].strip()
        raise RuntimeError(f'Native radio control exited {result.returncode} '
                           f'(0x{result.returncode & 0xffffffff:08X}): {detail}')
    try:
        answer = json.loads(result.stdout)
    except (ValueError, UnicodeError) as error:
        raise RuntimeError('Native radio control did not return valid JSON.') from error
    if not isinstance(answer, dict):
        raise RuntimeError('Native radio control did not return a device object.')
    try:
        reported_serial = device_serial(answer.get('serial'))
    except ValueError as error:
        raise RuntimeError('Native radio control returned an invalid device serial.') from error
    if not reported_serial or (selected and reported_serial != selected):
        raise RuntimeError('Native radio control did not confirm the designated device.')
    lines = [line.strip() for line in receipt.splitlines() if line.strip()]
    markers = ('done:open', 'done:close', 'done:exit')
    if any(marker not in lines for marker in markers):
        raise RuntimeError('Native radio control is missing checked shutdown receipts.')
    positions = [lines.index(marker) for marker in markers]
    if (positions != sorted(positions) or 'error:' in receipt.lower()
            or 'failed:' in receipt.lower() or answer.get('error')):
        raise RuntimeError('Native radio control did not confirm an ordered, successful shutdown.')
    if idle:
        if answer.get('idle') is not True:
            raise RuntimeError('Native radio control did not confirm RF mode OFF.')
    elif (answer.get('name') != 'HackRF One' or not isinstance(answer.get('firmware'), str)
          or not 1 <= len(answer['firmware']) <= 255 or not answer['firmware'].isascii()):
        raise RuntimeError('Native radio control did not return valid firmware information.')
    return answer


def main(argv=None):
    parser = argparse.ArgumentParser(description='Checked native HackRF info/idle control; no RX/TX.')
    parser.add_argument('--idle', action='store_true')
    parser.add_argument('--serial', type=device_serial)
    parser.add_argument('--tools-dir')
    args = parser.parse_args(argv)
    try:
        answer = run_control(idle=args.idle, serial=args.serial, tools_dir=args.tools_dir)
    except Exception as error:
        print(json.dumps({'error': str(error)}), flush=True)
        return 1
    print(json.dumps(answer), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
