"""Portable, standalone assets and user-writable application state."""

import os
from pathlib import Path
import re
import sys

APP_NAME = 'Anomaly SDR Studio'
VERSION = '0.1.0'
APP_VERSION = VERSION
ASSET_ROOT = (Path(sys._MEIPASS) if getattr(sys, 'frozen', False)
              else Path(__file__).resolve().parent)


def tools_directory(value=None):
    chosen = value or os.environ.get('ANOMALY_SDR_HACKRF_BIN')
    return (Path(chosen).expanduser().resolve() if chosen
            else ASSET_ROOT / 'tools' / 'hackrf' / 'bin')


def data_directory(value=None):
    if value:
        return Path(value).expanduser().resolve()
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / 'AppData' / 'Local'))
        return base / 'AnomalySDRStudio'
    return Path(os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local' / 'state')) / 'anomaly-sdr-studio'


def device_serial(value):
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-fA-F]{32}', value) is None:
        raise ValueError('HackRF serial must contain exactly 32 hexadecimal characters.')
    return value.lower()
