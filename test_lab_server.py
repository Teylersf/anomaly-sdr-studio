"""Offline lifecycle, provenance and local-control tests. Never opens an SDR.

Receiver and optional protocol subprocesses are replaced by fixtures. The
HTTP checks use an ephemeral loopback port, and frame checks run the actual
prime-packet modem on in-memory bytes. Receive child tests use byte pipes only.
"""

from contextlib import redirect_stderr
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

import lab_server as server
import radio_receiver


class OfflineReceiver:
    def __init__(self, *_args, **_kwargs):
        self.calls = []
        self.close_error = None
        self.config = {}
        self.bytes_received = 0

    def info(self):
        self.calls.append('info')
        raise AssertionError('Hardware discovery forbidden in an offline test')

    def receive(self, *_args, **_kwargs):
        self.calls.append('receive')
        raise AssertionError('Hardware reception forbidden in an offline test')

    def cancel(self):
        self.calls.append('cancel')

    def close(self):
        self.calls.append('close')
        if self.close_error:
            raise self.close_error


class OfflineProtocols:
    def capabilities(self):
        return {'modules': [{'id': 'fixture', 'name': 'Offline fixture',
                             'available': False, 'state': 'disabled'}],
                'last_attempt': None, 'last_error': None}

    def decode(self, *_args):
        return []


class LabFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='anomaly-sdr-signal-tests-')
        self.addCleanup(self.temporary.cleanup)
        self.receiver_patch = patch.object(server, 'Receiver', OfflineReceiver)
        self.protocol_patch = patch.object(server, 'AutomaticDecoders', OfflineProtocols)
        self.receiver_patch.start()
        self.protocol_patch.start()
        self.addCleanup(self.receiver_patch.stop)
        self.addCleanup(self.protocol_patch.stop)
        self.lab = server.Lab(Path(self.temporary.name) / 'runtime', offline=True)
        self.addCleanup(self.clean_lab)

    def clean_lab(self):
        self.lab.receiver.close_error = None
        # Failure fixtures may deliberately retain fake worker references.
        for name in ('worker', 'processor', 'protocol_worker'):
            worker = getattr(self.lab, name)
            if worker and not isinstance(worker, threading.Thread):
                setattr(self.lab, name, None)
        self.lab.stop()

    def await_condition(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.01)
        self.fail('Offline worker condition did not arrive within its deadline')


class ConfigurationTests(unittest.TestCase):
    def test_defaults_are_receive_only_and_bounded(self):
        config = server.validate_config({})
        self.assertEqual(config['sample_rate'], 8_000_000)
        self.assertEqual(config['frequency_hz'], 1_600_000_000)
        self.assertEqual((config['lna_gain'], config['vga_gain']), (16, 16))
        self.assertFalse(config['rf_amp'])
        self.assertIsNone(config['symbol_rate'])

    def test_setting_edges_and_previous_copy(self):
        original = server.validate_config({})
        changed = server.validate_config({'frequency_hz': 6_000_000_000,
                                          'lna_gain': 40, 'vga_gain': 62,
                                          'rf_amp': True, 'symbol_rate': 100_000}, original)
        self.assertEqual(changed['symbol_rate'], 100_000)
        self.assertEqual(original['frequency_hz'], 1_600_000_000)
        self.assertIsNone(original['symbol_rate'])
        self.assertEqual(server.validate_config({'frequency_hz': 1_000_000})['frequency_hz'], 1_000_000)

    def test_wrong_types_ranges_and_unknown_settings_rejected(self):
        invalid = [None, [], 'settings', {'frequency_hz': True},
                   {'frequency_hz': 1_600_000_000.0}, {'frequency_hz': 999_999},
                   {'frequency_hz': 6_000_000_001}, {'frequency_hz': '1600000000'},
                   {'lna_gain': 7}, {'lna_gain': 48}, {'lna_gain': False},
                   {'vga_gain': 3}, {'vga_gain': 64}, {'rf_amp': 1},
                   {'symbol_rate': True}, {'symbol_rate': 99},
                   {'symbol_rate': 100_001}, {'symbol_rate': float('nan')},
                   {'sample_rate': 20_000_000}, {'tx_gain': 47}, {'transmit': True}]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                server.validate_config(values)


class LifecycleTests(LabFixture):
    def test_offline_startup_is_idle_without_hardware_or_samples(self):
        snapshot = self.lab.snapshot()
        self.assertEqual((snapshot['mode'], snapshot['state']), ('idle', 'stopped'))
        self.assertFalse(snapshot['rf_tx_enabled'])
        self.assertFalse(snapshot['device']['available'])
        self.assertIsNone(snapshot['rx']['power_dbfs'])
        self.assertEqual(snapshot['rx']['samples_received'], 0)
        self.assertEqual(self.lab.receiver.calls, [])

    def test_offline_live_rejected_before_stopping_current_source(self):
        with self.assertRaisesRegex(ValueError, 'Offline'):
            self.lab.start('live')
        self.assertEqual(self.lab.receiver.calls, [])

    def test_invalid_mode_or_config_does_not_interrupt_existing_work(self):
        self.lab.data.update(mode='demo', state='receiving')
        for mode, values in [('transmit', {}), ('demo', {'tx_gain': 47}),
                             ('demo', {'frequency_hz': 0})]:
            with self.subTest(mode=mode, values=values), self.assertRaises(ValueError):
                self.lab.start(mode, values)
        self.assertEqual(self.lab.data['state'], 'receiving')
        self.assertEqual(self.lab.receiver.calls, [])

    def test_demo_labels_real_generated_samples_and_stop_joins_all_workers(self):
        self.lab.start('demo')
        workers = (self.lab.worker, self.lab.processor, self.lab.protocol_worker)
        self.await_condition(lambda: self.lab.snapshot()['analysis'] is not None)
        snapshot = self.lab.snapshot()
        self.assertEqual(snapshot['mode'], 'demo')
        self.assertIn('SYNTHETIC', snapshot['source_label'])
        self.assertGreater(snapshot['rx']['samples_received'], 0)
        self.assertIn('structure', snapshot['analysis'])
        self.assertFalse(snapshot['rf_tx_enabled'])
        self.lab.stop()
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(self.lab.data['state'], 'stopped')
        self.assertIsNone(self.lab.worker)
        self.assertIsNone(self.lab.processor)
        self.assertIsNone(self.lab.protocol_worker)
        self.assertNotIn('info', self.lab.receiver.calls)
        self.assertNotIn('receive', self.lab.receiver.calls)

    def test_stop_cancels_before_join_and_closes_after_every_worker(self):
        order = []
        self.lab.receiver.cancel = lambda: order.append('cancel')
        self.lab.receiver.close = lambda: order.append('close')
        for field, label in [('worker', 'source'), ('processor', 'analysis'),
                             ('protocol_worker', 'protocol')]:
            worker = Mock()
            worker.join.side_effect = lambda timeout, label=label: order.append(label)
            worker.is_alive.return_value = False
            setattr(self.lab, field, worker)
        self.lab.stop()
        self.assertEqual(order, ['cancel', 'source', 'analysis', 'protocol', 'close'])

    def test_unjoined_worker_prevents_false_stopped_receipt(self):
        worker = Mock()
        worker.is_alive.return_value = True
        self.lab.protocol_worker = worker
        self.lab.data['state'] = 'receiving'
        with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
            self.lab.stop()
        self.assertEqual(self.lab.data['state'], 'receiving')
        self.assertIs(self.lab.protocol_worker, worker)
        self.assertNotIn('close', self.lab.receiver.calls)

    def test_unconfirmed_native_close_preserves_running_state(self):
        self.lab.data['state'] = 'receiving'
        self.lab.receiver.close_error = RuntimeError('Native shutdown unconfirmed')
        with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
            self.lab.stop()
        self.assertEqual(self.lab.data['state'], 'receiving')

    def test_quit_admission_guard_precedes_all_source_starts(self):
        self.lab.quitting = True
        for mode in ('live', 'demo', 'replay'):
            with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, 'closing'):
                self.lab.start(mode)
        self.assertEqual(self.lab.receiver.calls, [])

    def test_gap_clears_decoder_windows_and_pending_analysis(self):
        self.lab.consume(bytes([1, 2]) * 100)
        self.lab.protocol_ring.append(b'fixture')
        self.lab.gap()
        self.assertEqual(len(self.lab.ring), 0)
        self.assertEqual(len(self.lab.protocol_ring), 0)
        self.assertIsNone(self.lab.latest_block)
        self.assertEqual(self.lab.data['rx']['capture_gaps'], 1)

    def test_consumption_after_stop_cannot_change_counters(self):
        self.lab.stop_event.set()
        self.assertEqual(self.lab.consume(b'\x01\x02'), -1)
        self.assertEqual(self.lab.data['rx']['samples_received'], 0)

    def test_complex_clipping_is_any_clipped_iq_component(self):
        # Every complex sample clips in I, while Q remains zero.
        self.lab.consume(np.column_stack((np.full(16384, 127), np.zeros(16384))).astype(np.int8).tobytes())
        self.lab.processor = threading.Thread(target=self.lab._process, daemon=True)
        self.lab.processor.start()
        self.await_condition(lambda: self.lab.snapshot()['analysis'] is not None)
        self.lab.stop()
        self.assertEqual(self.lab.data['rx']['clipped_fraction'], 1.0)

    def test_short_final_replay_block_skips_display_without_losing_decoder_bytes(self):
        raw = bytes([5, 9]) * 4608  # A valid tail shorter than the 8192-point FFT.
        self.lab.data.update(mode='replay', state='receiving')
        self.lab.consume(raw)
        self.lab.processor = threading.Thread(target=self.lab._process, daemon=True)
        self.lab.processor.start()
        self.await_condition(lambda: self.lab.snapshot()['rx']['dropped_analysis_blocks'] > 0)
        snapshot = self.lab.snapshot()
        self.assertEqual(snapshot['state'], 'receiving')
        self.assertIsNone(snapshot['error'])
        self.assertIsNone(snapshot['analysis'])
        self.assertEqual(snapshot['rx']['samples_received'], 4608)
        self.assertEqual(snapshot['rx']['bytes_received'], len(raw))
        self.assertGreater(len(b''.join(self.lab.ring)), 0)
        self.lab.stop()

    def test_repeated_stop_does_not_inflate_stopped_elapsed_time(self):
        self.lab.started = 10
        self.lab.data['state'] = 'receiving'
        with patch.object(server.time, 'monotonic', return_value=14):
            self.lab.stop()
        self.assertEqual(self.lab.data['rx']['elapsed_seconds'], 4)
        with patch.object(server.time, 'monotonic', return_value=29):
            self.lab.stop()
        self.assertEqual(self.lab.data['rx']['elapsed_seconds'], 4)

    def test_error_elapsed_is_frozen_through_later_stop(self):
        self.lab.started = 10
        self.lab.data['state'] = 'receiving'
        with patch.object(server.time, 'monotonic', return_value=14):
            self.lab.fail('Fixture receive error')
        self.assertEqual(self.lab.data['rx']['elapsed_seconds'], 4)
        with patch.object(server.time, 'monotonic', return_value=29):
            self.lab.stop()
        self.assertEqual(self.lab.data['rx']['elapsed_seconds'], 4)

    def test_replay_only_accepts_allowlisted_ids_and_declares_recorded_source(self):
        capture = {'id': 'fixture', 'name': 'fixture.iq', 'path': Path(self.temporary.name) / 'fixture.iq',
                   'recorded_at': '2026-10-03T18:00:00Z', 'frequency_hz': 433_920_000}
        with patch.object(server, 'captures', return_value=[capture]):
            for identifier in ('../secret', 'E:\\private.iq', 'missing'):
                with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                    self.lab.start('replay', capture_id=identifier)
            self.lab._replay = lambda _capture: self.lab.stop_event.wait()
            self.lab.start('replay', capture_id='fixture')
            snapshot = self.lab.snapshot()
            self.assertEqual(snapshot['mode'], 'replay')
            self.assertIn('Recorded I/Q', snapshot['source_label'])
            self.assertNotIn('SYNTHETIC', snapshot['source_label'])
            self.assertEqual(snapshot['config']['frequency_hz'], 433_920_000)
            self.assertNotIn('path', snapshot['captures'][0])


class PrimeDecodeTests(LabFixture):
    def fixture(self, payload):
        return server.encode_iq(payload, sample_rate=server.DECODE_RATE, amplitude=16)

    def test_actual_frame_decode_preserves_crc_and_synthetic_provenance(self):
        packet = server.build_packet(123, 5)
        self.lab.data['mode'] = 'demo'
        self.lab.ring.append(self.fixture(packet['payload_text'].encode('ascii')))
        self.lab.decode_ring()
        frames = self.lab.snapshot()['decoded']
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertTrue(frame['crc_valid'])
        self.assertTrue(frame['synthetic'])
        self.assertEqual(frame['source_mode'], 'demo')
        self.assertFalse(frame['source_authenticated'])
        self.assertEqual(frame['packet']['primes'], [2, 3, 5, 7, 11])
        self.assertEqual(frame['packet']['sequence'], 123)
        self.assertIn('formats', frame)

    def test_actual_frame_deduplication_and_recorded_source(self):
        packet = server.build_packet(321, 3)
        self.lab.data['mode'] = 'replay'
        self.lab.ring.append(self.fixture(packet['payload_text'].encode('ascii')))
        self.lab.decode_ring()
        self.lab.decode_ring()
        frames = self.lab.snapshot()['decoded']
        self.assertEqual(len(frames), 1)
        self.assertFalse(frames[0]['synthetic'])
        self.assertEqual(frames[0]['source_mode'], 'replay')
        self.assertFalse(frames[0]['source_authenticated'])

    def test_chunked_full_rate_iq_keeps_decimation_clock_across_uneven_reads(self):
        packet = server.build_packet(456, 3)
        raw = server.encode_iq(packet['payload_text'].encode('ascii'),
                               sample_rate=server.SAMPLE_RATE, amplitude=16)
        self.lab.data['mode'] = 'replay'
        # 32777 complex samples/read is deliberately not a multiple of 25.
        for start in range(0, len(raw), 65554):
            self.lab.consume(raw[start:start + 65554])
        self.lab.decode_ring()
        frames = self.lab.snapshot()['decoded']
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]['packet']['sequence'], 456)
        self.assertTrue(frames[0]['crc_valid'])
        self.assertEqual(self.lab.data['rx']['bytes_received'], len(raw))

    def test_valid_modem_crc_does_not_admit_invalid_prime_crc_or_arbitrary_text(self):
        packet = server.build_packet(123, 2)['payload_text'].encode('ascii')
        invalid = [packet.replace(b'PRIMES=2,3', b'PRIMES=2,5'),
                   b'Hello from an unknown source', b'\xff\xfe\x80']
        for payload in invalid:
            with self.subTest(payload=payload):
                self.lab.ring.clear()
                self.lab.ring.append(self.fixture(payload))
                self.lab.decode_ring()
                self.assertEqual(self.lab.data['decoded'], [])


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='anomaly-sdr-capture-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'studio-data'
        self.root.mkdir()
        self.folder = self.root / 'captures'
        self.folder.mkdir()
        self.root_patch = patch.object(server, 'data_directory', return_value=self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def capture(self, name, raw=b'\x01\x02' * 5, metadata=None):
        path = self.folder / (name + '.iq')
        path.write_bytes(raw)
        if metadata is None:
            metadata = {'sampleRateHz': server.SAMPLE_RATE, 'bytes': len(raw),
                        'format': 'signed 8-bit interleaved I,Q; no header',
                        'frequencyHz': 1_600_000_000, 'startedUtc': '2026-10-03T18:00:00Z'}
        path.with_suffix('.json').write_text(json.dumps(metadata), encoding='utf-8')
        return path

    def test_only_complete_compatible_sidecar_described_iq_is_admitted(self):
        good = self.capture('valid')
        self.capture('odd', raw=b'123')
        self.capture('wrong-rate', metadata={'sampleRateHz': 1_000_000})
        self.capture('wrong-format', metadata={'sampleRateHz': server.SAMPLE_RATE, 'bytes': 10,
                                              'format': 'unsigned', 'frequencyHz': 1_600_000_000})
        self.capture('wrong-frequency', metadata={'sampleRateHz': server.SAMPLE_RATE, 'bytes': 10,
                            'format': 'signed 8-bit interleaved I,Q; no header', 'frequencyHz': True})
        (self.folder / 'no-sidecar.iq').write_bytes(b'12')
        found = server.captures()
        self.assertEqual([item['id'] for item in found], ['valid'])
        self.assertEqual(found[0]['path'], good)
        self.assertEqual(found[0]['duration_seconds'], 10 / 2 / server.SAMPLE_RATE)

    def test_non_object_and_malformed_sidecars_are_ignored(self):
        for index, metadata in enumerate([[], 'text', 7, True]):
            self.capture('invalid-' + str(index), metadata=metadata)
        invalid_json = self.capture('malformed')
        invalid_json.with_suffix('.json').write_text('{', encoding='utf-8')
        self.capture('valid')
        self.assertEqual([item['id'] for item in server.captures()], ['valid'])

    def test_capture_listing_has_a_finite_selection_bound(self):
        for index in range(35):
            self.capture(f'fixture-{index:03d}')
        self.assertEqual(len(server.captures()), 30)


class LocalHTTPTests(LabFixture):
    def setUp(self):
        super().setUp()
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.http.daemon_threads = True
        self.http.lab = self.lab
        self.http_thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.http_thread.start()
        self.addCleanup(self.close_http)

    def close_http(self):
        self.http.shutdown()
        self.http_thread.join(timeout=2)
        self.http.server_close()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=3)
        try:
            content = None if body is None else body if isinstance(body, (str, bytes)) else json.dumps(body)
            connection.request(method, path, body=content, headers=headers or {})
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b'{}')
        finally:
            connection.close()

    def test_status_requires_exact_local_host(self):
        for host in ('evil.example', '127.0.0.1.evil.example', 'localhost:80',
                     f'evil.example:{self.http.server_port}'):
            with self.subTest(host=host):
                code, _ = self.request('GET', '/api/status', headers={'Host': host})
                self.assertEqual(code, 403)
        code, snapshot = self.request('GET', '/api/status')
        self.assertEqual(code, 200)
        self.assertFalse(snapshot['rf_tx_enabled'])

    def test_foreign_origin_and_host_cannot_control(self):
        for headers in ({'Origin': 'https://evil.example'}, {'Origin': 'null'},
                        {'Origin': 'http://127.0.0.1:80'}, {'Host': 'evil.example'}):
            with self.subTest(headers=headers):
                code, _ = self.request('POST', '/api/demo', {}, headers)
                self.assertEqual(code, 403)
        self.assertEqual(self.lab.receiver.calls, [])

    def test_same_origin_control_and_receive_only_routes(self):
        origin = {'Origin': f'http://127.0.0.1:{self.http.server_port}'}
        for path in ('/api/transmit', '/api/tx', '/api/loop/start'):
            with self.subTest(path=path):
                code, _ = self.request('POST', path, {}, origin)
                self.assertEqual(code, 404)
        code, snapshot = self.request('POST', '/api/stop', {}, origin)
        self.assertEqual(code, 200)
        self.assertFalse(snapshot['rf_tx_enabled'])
        self.assertNotIn('receive', self.lab.receiver.calls)

    def test_control_body_types_sizes_and_invalid_receiver_config_rejected(self):
        for body in ('[]', '{', '"configuration"', 'x' * 4097):
            with self.subTest(body=body[:20]):
                code, _ = self.request('POST', '/api/start', body)
                self.assertEqual(code, 400)
        for body in ({'frequency_hz': True}, {'tx_gain': 47}, {'rf_amp': 1},
                     {'symbol_rate': 100_001}):
            with self.subTest(body=body):
                code, _ = self.request('POST', '/api/start', body)
                self.assertEqual(code, 400)
        self.assertEqual(self.lab.receiver.calls, [])

    def test_waterfall_cursor_rejects_ambiguous_or_unbounded_values(self):
        for query in ('since=x', 'since=1&since=2', 'since=' + '9' * 21):
            with self.subTest(query=query):
                code, _ = self.request('GET', '/api/waterfall?' + query)
                self.assertEqual(code, 400)
        code, result = self.request('GET', '/api/waterfall?since=-1')
        self.assertEqual(code, 200)
        self.assertEqual(result['rows'], [])

    def test_closing_guard_blocks_queued_http_mutations(self):
        self.lab.quitting = True
        for path in ('/api/demo', '/api/replay', '/api/start', '/api/baseline/reset',
                     '/api/decoder/config'):
            with self.subTest(path=path):
                code, result = self.request('POST', path, {})
                self.assertEqual(code, 500)
                self.assertIn('closing', result['error'])
        self.assertEqual(self.lab.receiver.calls, [])

    def test_failed_quit_stays_open_and_allows_checked_retry(self):
        self.lab.receiver.close_error = RuntimeError('Fixture shutdown unconfirmed')
        code, result = self.request('POST', '/api/quit', {})
        self.assertEqual(code, 500)
        self.assertNotIn('closed', result)
        self.assertFalse(self.lab.quitting)
        self.assertTrue(self.http_thread.is_alive())
        self.assertEqual(self.request('GET', '/api/status')[0], 200)
        self.lab.receiver.close_error = None
        code, result = self.request('POST', '/api/quit', {})
        self.assertEqual(code, 200)
        self.assertTrue(result['closed'])
        self.assertTrue(self.lab.quitting)
        self.assertEqual(result['state'], 'stopped')
        self.assertFalse(result['rf_tx_enabled'])
        self.http_thread.join(timeout=2)
        self.assertFalse(self.http_thread.is_alive())
        with self.assertRaisesRegex(RuntimeError, 'closing'):
            self.lab.start('demo')


class MockReceiveProcess:
    def __init__(self, raw, receipt=b'Stop with Ctrl-C\n'):
        self.stdout = io.BytesIO(raw)
        self.stderr = io.BytesIO(receipt)
        self.returncode = 0
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -1


class ReceiverCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='anomaly-sdr-receiver-fixture-')
        self.addCleanup(self.temporary.cleanup)
        # Constructor only validates config and initializes locks; no discovery.
        self.receiver = radio_receiver.Receiver(Path(self.temporary.name),
                                                Path(self.temporary.name) / 'tools', '0' * 32)

    def run_receive_fixture(self, raw, config=None):
        child = MockReceiveProcess(raw)
        self.receiver.config = config or {'lna_gain': 16, 'vga_gain': 16, 'rf_amp': False}
        received = []
        with patch.object(radio_receiver.subprocess, 'Popen', return_value=child) as launch:
            self.receiver.receive(1_600_000_000, lambda data: received.append(data) or 0,
                                  threading.Event(), duration=2)
            self.receiver.reader.join(timeout=2)
            self.receiver.stderr_reader.join(timeout=2)
            self.assertFalse(self.receiver.reader.is_alive())
            self.assertFalse(self.receiver.stderr_reader.is_alive())
            return launch.call_args.args[0], received

    def test_command_is_receive_only_with_port_power_off_and_finite_target(self):
        command, received = self.run_receive_fixture(b'\x01\x02' * 16,
                                {'lna_gain': 40, 'vga_gain': 62, 'rf_amp': True})
        self.assertIn('-r', command)
        self.assertEqual(command[command.index('-r') + 1], '-')
        self.assertNotIn('-t', command)
        self.assertNotIn('-c', command)
        self.assertNotIn('-R', command)
        self.assertEqual(command[command.index('-p') + 1], '0')
        self.assertEqual(command[command.index('-a') + 1], '1')
        self.assertEqual(command[command.index('-l') + 1], '40')
        self.assertEqual(command[command.index('-g') + 1], '62')
        self.assertEqual(command[command.index('-n') + 1], str(2 * radio_receiver.checked.SAMPLE_RATE))
        self.assertEqual(received, [b'\x01\x02' * 16])

    def test_default_receive_amp_off_and_incomplete_iq_is_rejected(self):
        command, received = self.run_receive_fixture(b'\x01\x02\x03')
        self.assertEqual(command[command.index('-a') + 1], '0')
        self.assertEqual(received, [])
        self.assertIn('partial I/Q', self.receiver.reader_error)

    def test_set_stop_event_or_owned_process_prevents_subprocess_launch(self):
        stop = threading.Event()
        stop.set()
        with patch.object(radio_receiver.subprocess, 'Popen') as launch:
            self.receiver.receive(1_600_000_000, lambda _raw: 0, stop)
            launch.assert_not_called()
        self.receiver.process = MockReceiveProcess(b'')
        with patch.object(radio_receiver.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, 'overlapping'):
                self.receiver.receive(1_600_000_000, lambda _raw: 0, threading.Event())
            launch.assert_not_called()


class ReceiverPollTests(unittest.TestCase):
    NOT_FOUND = 'hackrf_open() failed: HackRF not found (-5)'

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='anomaly-sdr-poll-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.receiver = radio_receiver.Receiver(Path(self.temporary.name),
                                                Path(self.temporary.name) / 'tools', '0' * 32)
        self.receiver.process = MockReceiveProcess(b'')
        self.receiver.process.returncode = 1
        self.receiver.stderr_tail.append(self.NOT_FOUND)

    def test_exact_not_found_before_streaming_and_without_iq_is_recoverable(self):
        with self.assertRaises(radio_receiver.checked.ReceiveOpenError) as caught:
            self.receiver.poll()
        self.assertEqual(str(caught.exception), self.NOT_FOUND)
        self.assertEqual(self.receiver.bytes_received, 0)
        self.assertFalse(self.receiver.stream_started.is_set())

    def assert_not_new_open_error(self):
        with self.assertRaises(RuntimeError) as caught:
            self.receiver.poll()
        self.assertNotIsInstance(caught.exception, radio_receiver.checked.ReceiveOpenError)

    def test_mixed_or_changed_not_found_receipts_are_not_retried(self):
        for receipt in ([self.NOT_FOUND, 'call hackrf_set_freq(1600000000 Hz)'],
                        ['warning: startup failed', self.NOT_FOUND],
                        [self.NOT_FOUND, self.NOT_FOUND],
                        ['hackrf_open() failed: HackRF not found (-6)'],
                        ['hackrf_open() failed: Device unavailable (-5)']):
            with self.subTest(receipt=receipt):
                self.receiver.stderr_tail.clear()
                self.receiver.stderr_tail.extend(receipt)
                self.assert_not_new_open_error()

    def test_any_stream_started_or_delivered_iq_precludes_not_found_retry(self):
        self.receiver.stream_started.set()
        self.assert_not_new_open_error()
        self.receiver.stream_started.clear()
        for count in (1, 2, 262144):
            with self.subTest(bytes_received=count):
                self.receiver.bytes_received = count
                self.assert_not_new_open_error()

    def test_other_exit_codes_or_reader_failure_preclude_not_found_retry(self):
        for code in (2, -1, 3221226505):
            with self.subTest(exit_code=code):
                self.receiver.process.returncode = code
                self.assert_not_new_open_error()
        self.receiver.process.returncode = 1
        self.receiver.reader_error = 'Receive pipe returned malformed IQ'
        self.assert_not_new_open_error()

    def test_live_readers_or_running_child_delay_classification(self):
        reader = Mock()
        reader.is_alive.return_value = True
        self.receiver.stderr_reader = reader
        self.assertIsNone(self.receiver.poll())
        self.receiver.stderr_reader = None
        self.receiver.process.returncode = None
        self.assertIsNone(self.receiver.poll())

    def test_existing_access_denied_retry_and_finite_timer_rules_are_preserved(self):
        self.receiver.stderr_tail.clear()
        self.receiver.stderr_tail.append('hackrf_open() failed: Access denied (insufficient permissions) (-1000)')
        with self.assertRaises(radio_receiver.checked.ReceiveOpenError):
            self.receiver.poll()
        self.receiver.stderr_tail.clear()
        self.receiver.stderr_tail.extend(["Couldn't transfer any bytes for one second.",
                    'hackrf_stop_rx() done', 'hackrf_close() done', 'hackrf_exit() done'])
        self.receiver.stream_started.set()
        self.receiver.expected_bytes = 32
        self.receiver.bytes_received = 32
        self.assertEqual(self.receiver.poll(), 0)
        self.assertIn('Finite RX target complete', self.receiver.completion_warning)
        self.receiver.bytes_received = 30
        with self.assertRaises(radio_receiver.checked.ReceiveStallError):
            self.receiver.poll()


class ScriptedStopEvent:
    """A deterministic stop event; records cooldowns without sleeping."""
    def __init__(self, stop_after_cooldowns=None):
        self.stopped = False
        self.waits = []
        self.cooldowns = 0
        self.stop_after_cooldowns = stop_after_cooldowns

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, duration):
        self.waits.append(duration)
        if duration >= .25:
            self.cooldowns += 1
            if self.stop_after_cooldowns == self.cooldowns:
                self.set()
        return self.stopped


class ScriptedLiveReceiver(OfflineReceiver):
    def __init__(self, outcomes, order, cleanup_error=False):
        super().__init__()
        self.outcomes = iter(outcomes)
        self.order = order
        self.durations = []
        self.cleanup_error = cleanup_error
        self.closed_once = False
        self.current = None

    def receive(self, frequency, _callback, _stop_event, duration=None):
        self.order.append('receive')
        self.durations.append(duration)
        self.current = next(self.outcomes)
        self.bytes_received = server.SAMPLE_RATE * server.RECEIVE_WINDOW_SECONDS * 2
        if self.current == 'open-error':
            self.bytes_received = 0
            raise server.checked.ReceiveOpenError('Exact fixture startup failure')
        if self.current == 'partial':
            self.bytes_received -= 2

    def poll(self):
        if self.current == 'generic-error':
            raise RuntimeError('Fixture mixed startup/transfer error')
        if self.current == 'pending':
            return None
        return 0

    def cancel(self):
        self.order.append('cancel')

    def close(self):
        self.order.append('close')
        if self.cleanup_error and not self.closed_once:
            self.closed_once = True
            raise RuntimeError('Fixture cleanup unconfirmed')


class LiveRecoveryTests(LabFixture):
    def fixture(self, outcomes, stop_after_cooldowns=None, cleanup_error=False):
        order = []
        self.lab.receiver = ScriptedLiveReceiver(outcomes, order, cleanup_error)
        self.lab.stop_event = ScriptedStopEvent(stop_after_cooldowns)
        self.lab.data.update(mode='live', state='receiving')
        self.lab.decode_ring = lambda: order.append('decode')
        self.lab._live()
        return order

    def test_three_consecutive_open_failures_stop_and_cleanup_before_every_retry(self):
        order = self.fixture(['open-error'] * 3)
        self.assertEqual(order, ['receive', 'close', 'receive', 'close',
                                 'receive', 'close', 'cancel', 'close'])
        self.assertEqual(self.lab.receiver.durations, [server.RECEIVE_WINDOW_SECONDS] * 3)
        self.assertEqual([value for value in self.lab.stop_event.waits if value >= .25], [.5, 1.0])
        self.assertEqual(self.lab.data['rx']['open_retries'], 2)
        self.assertEqual(self.lab.data['state'], 'error')
        self.assertTrue(self.lab.stop_event.is_set())

    def test_complete_finite_window_uses_current_target_and_normal_cooldown(self):
        order = self.fixture(['complete'], stop_after_cooldowns=1)
        self.assertEqual(order, ['receive', 'decode', 'close', 'close'])
        self.assertEqual(self.lab.receiver.bytes_received,
                         server.SAMPLE_RATE * server.RECEIVE_WINDOW_SECONDS * 2)
        self.assertEqual([value for value in self.lab.stop_event.waits if value >= .25], [.25])
        self.assertEqual(self.lab.data['rx']['open_retries'], 0)

    def test_successful_window_resets_consecutive_retry_budget(self):
        order = self.fixture(['open-error', 'complete', 'open-error', 'open-error', 'open-error'])
        self.assertEqual(order.count('receive'), 5)
        self.assertEqual(order.count('decode'), 1)
        self.assertEqual([value for value in self.lab.stop_event.waits if value >= .25], [.5, .25, .5, 1.0])
        self.assertEqual(self.lab.data['rx']['open_retries'], 3)
        self.assertEqual(self.lab.data['state'], 'error')

    def test_partial_capture_and_generic_error_are_not_blindly_retried(self):
        for outcome, message in [('partial', 'finite sample target'),
                                 ('generic-error', 'mixed startup/transfer error')]:
            with self.subTest(outcome=outcome):
                self.lab.stop_event = threading.Event()
                order = self.fixture([outcome])
                self.assertEqual(order.count('receive'), 1)
                self.assertNotIn('decode', order)
                self.assertEqual(self.lab.data['rx']['open_retries'], 0)
                self.assertIn(message, self.lab.data['error'])

    def test_failed_checked_cleanup_blocks_reopen(self):
        order = self.fixture(['open-error'], cleanup_error=True)
        self.assertEqual(order.count('receive'), 1)
        self.assertEqual(order.count('close'), 2)
        self.assertIn('cleanup unconfirmed', self.lab.data['error'])
        self.assertEqual([value for value in self.lab.stop_event.waits if value >= .25], [])

    def test_finite_deadline_stops_a_child_that_never_completes(self):
        deadline = server.RECEIVE_WINDOW_SECONDS * 2 + 4
        with patch.object(server.time, 'monotonic', side_effect=[10, 10 + deadline + 1,
                                                               10 + deadline + 1]):
            order = self.fixture(['pending'])
        self.assertEqual(order.count('receive'), 1)
        self.assertNotIn('decode', order)
        self.assertIn('finite deadline', self.lab.data['error'])


class EntrypointTests(unittest.TestCase):
    def test_default_main_is_idle_and_server_binds_only_loopback(self):
        fake_lab = Mock()
        fake_server = Mock()
        with patch.object(server, 'Lab', return_value=fake_lab), \
             patch.object(server, 'ThreadingHTTPServer', return_value=fake_server) as create, \
             patch('builtins.print'):
            server.main(['--offline', '--port', '18788'])
        create.assert_called_once_with(('127.0.0.1', 18788), server.Handler)
        fake_lab.start.assert_not_called()
        fake_lab.stop.assert_called_once()
        fake_server.server_close.assert_called_once()

    def test_offline_receive_and_invalid_ports_rejected_before_any_lab(self):
        for args in (['--offline', '--receive'], ['--port', '0'], ['--port', '65536']):
            with self.subTest(args=args), patch.object(server, 'Lab') as construct, \
                 redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                server.main(args)
            construct.assert_not_called()


if __name__ == '__main__':
    unittest.main()
