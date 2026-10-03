"""Launcher behavior tests with server, browser, and radio calls replaced."""

import builtins
from contextlib import redirect_stdout
from io import StringIO
import json
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

import launch


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.server = ModuleType('lab_server')
        self.server.main = Mock(return_value=37)
        self.control = ModuleType('radio_control')
        self.control.main = Mock(return_value=19)
        self.modules = patch.dict(sys.modules, {'lab_server': self.server,
                                               'radio_control': self.control})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.status = self.start_patch('status', return_value=None)
        self.browser = self.start_patch('webbrowser.open')
        self.thread = self.start_patch('threading.Thread')

    def start_patch(self, name, **kwargs):
        patcher = patch('launch.' + name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def existing(self, *, offline=False, mode='idle', state='idle', application=None):
        return {'application_name': application or launch.APP_NAME,
                'offline': offline, 'mode': mode, 'state': state}

    def assert_no_service_actions(self):
        self.status.assert_not_called()
        self.browser.assert_not_called()
        self.thread.assert_not_called()
        self.server.main.assert_not_called()

    def test_radio_control_dispatches_first_without_server_status_browser_or_thread(self):
        original_import = builtins.__import__
        def reject_server(name, *args, **kwargs):
            if name == 'lab_server':
                raise AssertionError('Native dispatch must not import the service')
            return original_import(name, *args, **kwargs)
        args = ['--radio-control', '--serial', 'a' * 32, '--tools-dir', 'offline tools', '--idle']
        with patch('builtins.__import__', side_effect=reject_server):
            self.assertEqual(launch.main(args), 19)
        self.control.main.assert_called_once_with(args[1:])
        self.assert_no_service_actions()

    def test_version_prints_without_importing_server_or_control_or_accessing_hardware(self):
        original_import = builtins.__import__
        def reject_radio_modules(name, *args, **kwargs):
            if name in ('lab_server', 'radio_control', 'radio_backend', 'radio_receiver'):
                raise AssertionError('Version must not import radio modules')
            return original_import(name, *args, **kwargs)
        stream = StringIO()
        with patch('builtins.__import__', side_effect=reject_radio_modules), redirect_stdout(stream):
            self.assertEqual(launch.main(['--version']), 0)
        self.assertEqual(stream.getvalue().strip(), launch.APP_NAME + ' ' + launch.VERSION)
        self.assert_no_service_actions()
        self.control.main.assert_not_called()

    def test_help_delegates_to_parser_without_service_probe_or_browser(self):
        for flag in ('--help', '-h'):
            with self.subTest(flag=flag):
                self.assertEqual(launch.main([flag, '--no-browser']), 37)
                self.server.main.assert_called_with([flag])
        self.status.assert_not_called()
        self.browser.assert_not_called()
        self.thread.assert_not_called()

    def test_fresh_instance_starts_readiness_thread_and_forwards_arguments(self):
        args = ['--port', '8890', '--offline', '--demo', '--data-dir', 'local state']
        self.assertEqual(launch.main(args), 37)
        self.status.assert_called_once_with(8890)
        self.server.main.assert_called_once_with(args)
        self.thread.assert_called_once_with(target=launch.open_when_ready, args=(8890,), daemon=True)
        self.thread.return_value.start.assert_called_once()
        self.browser.assert_not_called()

    def test_fresh_no_browser_instance_does_not_spawn_a_browser_thread(self):
        self.assertEqual(launch.main(['--no-browser', '--offline']), 37)
        self.server.main.assert_called_once_with(['--offline'])
        self.status.assert_called_once_with(8788)
        self.thread.assert_not_called()
        self.browser.assert_not_called()

    def test_matching_existing_normal_instance_reuses_without_starting_radio(self):
        self.status.return_value = self.existing()
        self.assertEqual(launch.main([]), 0)
        self.browser.assert_called_once_with('http://127.0.0.1:8788/')
        self.server.main.assert_not_called()
        self.control.main.assert_not_called()
        self.thread.assert_not_called()

    def test_matching_existing_offline_demo_is_reused(self):
        self.status.return_value = self.existing(offline=True, mode='demo', state='analyzing')
        self.assertEqual(launch.main(['--offline', '--demo', '--no-browser']), 0)
        self.browser.assert_not_called()
        self.server.main.assert_not_called()
        self.thread.assert_not_called()

    def test_existing_live_receive_is_reused_only_when_already_receiving(self):
        self.status.return_value = self.existing(mode='live', state='receiving')
        self.assertEqual(launch.main(['--receive', '--no-browser']), 0)
        self.server.main.assert_not_called()
        self.browser.assert_not_called()
        self.control.main.assert_not_called()

    def test_foreign_app_is_never_reused(self):
        self.status.return_value = self.existing(application='Another application')
        with self.assertRaisesRegex(RuntimeError, 'another app'):
            launch.main([])
        self.server.main.assert_not_called()
        self.browser.assert_not_called()

    def test_offline_mode_mismatch_is_rejected_in_both_directions(self):
        for running_offline, args in ((True, []), (False, ['--offline'])):
            with self.subTest(running_offline=running_offline):
                self.status.return_value = self.existing(offline=running_offline)
                with self.assertRaisesRegex(RuntimeError, 'offline mode'):
                    launch.main(args)
        self.browser.assert_not_called()
        self.server.main.assert_not_called()

    def test_explicit_demo_does_not_silently_reuse_idle_live_or_replay(self):
        for mode in ('idle', 'live', 'replay'):
            with self.subTest(mode=mode):
                self.status.return_value = self.existing(mode=mode)
                with self.assertRaisesRegex(RuntimeError, 'not running the demo'):
                    launch.main(['--demo'])
        self.browser.assert_not_called()
        self.server.main.assert_not_called()

    def test_explicit_receive_does_not_silently_reuse_idle_demo_replay_or_error(self):
        for mode, state in (('idle', 'idle'), ('demo', 'analyzing'),
                            ('replay', 'analyzing'), ('live', 'error')):
            with self.subTest(mode=mode, state=state):
                self.status.return_value = self.existing(mode=mode, state=state)
                with self.assertRaisesRegex(RuntimeError, 'Start receive'):
                    launch.main(['--receive'])
        self.browser.assert_not_called()
        self.server.main.assert_not_called()

    def test_equals_style_port_is_used_for_reuse_and_forwarded_to_fresh_service(self):
        self.status.return_value = self.existing()
        self.assertEqual(launch.main(['--port=8891', '--no-browser']), 0)
        self.status.assert_called_once_with(8891)
        self.status.reset_mock(return_value=True)
        self.status.return_value = None
        self.assertEqual(launch.main(['--port=8892', '--no-browser']), 37)
        self.status.assert_called_once_with(8892)
        self.server.main.assert_called_once_with(['--port=8892'])

    def test_invalid_ports_are_rejected_before_service_probe(self):
        for value in ('0', '65536', '-1', 'not-a-number', ''):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    launch.main(['--port=' + value])
        self.assert_no_service_actions()

    def test_conflicting_modes_are_rejected_before_existing_instance_reuse(self):
        # Even an inconsistent existing response cannot convert contradictory
        # command-line requests into a successful browser-only reuse.
        for args in (['--offline', '--receive'], ['--demo', '--receive'],
                     ['--offline', '--demo', '--receive']):
            with self.subTest(args=args):
                self.status.return_value = self.existing(offline=True, mode='live', state='receiving')
                with self.assertRaisesRegex(ValueError, '[Cc]onflicting source modes'):
                    launch.main(args)
        self.assert_no_service_actions()

    def test_conflicting_modes_are_rejected_before_fresh_service_or_readiness_thread(self):
        for args in (['--offline', '--receive'], ['--demo', '--receive']):
            with self.subTest(args=args):
                with self.assertRaisesRegex(ValueError, '[Cc]onflicting source modes'):
                    launch.main(args)
        self.assert_no_service_actions()

    def test_readiness_wait_opens_only_the_matching_application(self):
        self.status.side_effect = [None, self.existing(application='Another application'), self.existing()]
        with patch.object(launch.time, 'sleep') as sleep:
            launch.open_when_ready(8895)
        self.assertEqual(self.status.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.browser.assert_called_once_with('http://127.0.0.1:8895/')

    def test_readiness_wait_is_bounded_when_service_never_appears(self):
        with patch.object(launch.time, 'sleep') as sleep:
            launch.open_when_ready(8895)
        self.assertEqual(self.status.call_count, 80)
        self.assertEqual(sleep.call_count, 80)
        self.browser.assert_not_called()


class LocalStatusProbeTests(unittest.TestCase):
    def response(self, payload):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = payload
        return response

    def test_actual_status_probe_uses_bounded_loopback_request(self):
        result = {'application_name': launch.APP_NAME}
        reply = self.response(json.dumps(result).encode())
        with patch.object(launch.urllib.request, 'urlopen', return_value=reply) as open_url:
            self.assertEqual(launch.status(8899), result)
        open_url.assert_called_once_with('http://127.0.0.1:8899/api/status', timeout=1)
        reply.read.assert_called_once_with(2 * 1024 * 1024 + 1)

    def test_unavailable_invalid_or_oversized_status_does_not_become_an_instance(self):
        with patch.object(launch.urllib.request, 'urlopen', side_effect=OSError('offline')):
            self.assertIsNone(launch.status(8899))
        for payload in (b'not JSON', b'[]', b'null', b'x' * (2 * 1024 * 1024 + 1)):
            with self.subTest(bytes=len(payload)):
                with patch.object(launch.urllib.request, 'urlopen', return_value=self.response(payload)):
                    self.assertIsNone(launch.status(8899))


if __name__ == '__main__':
    unittest.main()
