"""Portable entry point: native helper, local service, and browser launch."""
import json
import sys
import threading
import time
import urllib.request
import webbrowser
from app_config import APP_NAME, VERSION


def option_value(arguments, flag, default):
    for argument in arguments:
        if argument.startswith(flag + '='):
            return argument[len(flag) + 1:]
    try:
        return arguments[arguments.index(flag) + 1]
    except (ValueError, IndexError):
        return default


def status(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status', timeout=1) as reply:
            payload = reply.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024:
            return None
        result = json.loads(payload)
        return result if isinstance(result, dict) else None
    except (OSError, ValueError):
        return None


def compatible(existing, arguments):
    if existing.get('application_name') != APP_NAME:
        raise RuntimeError('That port belongs to another app; choose --port.')
    if ('--offline' in arguments) != bool(existing.get('offline')):
        raise RuntimeError('A different offline mode is running. Choose Quit app before switching launchers.')
    if '--demo' in arguments and existing.get('mode') != 'demo':
        raise RuntimeError('The existing instance is not running the demo. Choose Quit app or start Demo signal scene in its UI.')
    if '--receive' in arguments and (existing.get('mode') != 'live' or existing.get('state') != 'receiving'):
        raise RuntimeError('An instance is already open. Use Start receive or choose Quit app before --receive.')


def open_when_ready(port):
    for _ in range(80):
        ready = status(port)
        if ready and ready.get('application_name') == APP_NAME:
            webbrowser.open(f'http://127.0.0.1:{port}/')
            return
        time.sleep(.25)


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ['--radio-control']:
        import radio_control
        return radio_control.main(arguments[1:])
    if '--version' in arguments:
        print(f'{APP_NAME} {VERSION}')
        return 0
    browser_requested = '--no-browser' not in arguments
    arguments = [argument for argument in arguments if argument != '--no-browser']
    if '--help' in arguments or '-h' in arguments:
        from lab_server import main as run
        return run(arguments)
    if '--receive' in arguments and ('--offline' in arguments or '--demo' in arguments):
        raise ValueError('Conflicting source modes: --receive cannot be combined with --offline or --demo.')
    port = int(option_value(arguments, '--port', 8788))
    if not 1 <= port <= 65535:
        raise ValueError('Port must be between 1 and 65535.')
    existing = status(port)
    if existing:
        compatible(existing, arguments)
        if browser_requested:
            webbrowser.open(f'http://127.0.0.1:{port}/')
        return 0
    if browser_requested:
        threading.Thread(target=open_when_ready, args=(port,), daemon=True).start()
    from lab_server import main as run
    return run(arguments)


if __name__ == '__main__':
    raise SystemExit(main())
