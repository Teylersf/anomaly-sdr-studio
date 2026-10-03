"""Local raw-IQ receive, replay and signal analysis; no radio transmission."""

import argparse
from collections import deque
import copy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlsplit

import numpy as np

from radio_receiver import Receiver, checked
from signal_analysis import SignalAnalyzer
from noise_structure import analyze_structure
from temporal_patterns import TemporalPatterns
from auto_decoders import AutomaticDecoders, inspect_payload
from modem import decode_iq, encode_iq
from prime_packets import build_packet, decode_packet
from waterfall import WaterfallHistory
from app_config import APP_NAME, VERSION, ASSET_ROOT, data_directory

ROOT = ASSET_ROOT
SAMPLE_RATE = 8_000_000
DECODE_RATE = 320_000
CAPTURE_LIMIT = 256 * 1024 * 1024
RECEIVE_WINDOW_SECONDS = 30


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def validate_config(values, previous=None):
    config = dict(previous or {'frequency_hz': 1_600_000_000, 'sample_rate': SAMPLE_RATE,
                              'lna_gain': 16, 'vga_gain': 16, 'rf_amp': False, 'symbol_rate': None})
    if not isinstance(values, dict):
        raise ValueError('Configuration must be an object.')
    allowed = {'frequency_hz', 'lna_gain', 'vga_gain', 'rf_amp', 'symbol_rate'}
    if set(values) - allowed:
        raise ValueError('Unknown receiver setting.')
    config.update(values)
    for key, low, high, step in [('frequency_hz', 1_000_000, 6_000_000_000, 1),
                                ('lna_gain', 0, 40, 8), ('vga_gain', 0, 62, 2)]:
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high or value % step:
            raise ValueError(f'Invalid {key}; range {low}–{high}, step {step}.')
    if not isinstance(config['rf_amp'], bool):
        raise ValueError('RF amplifier must be true or false.')
    value = config['symbol_rate']
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 100_000):
        raise ValueError('Candidate symbol rate must be 100–100000 or null.')
    return config


def captures(folder=None):
    """Only sidecar-described local captures are admitted; never accept user paths."""
    result = []
    folder = Path(folder) if folder is not None else data_directory() / 'captures'
    for path in sorted(folder.glob('*.iq'), reverse=True):
        try:
            metadata = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
            if not isinstance(metadata, dict):
                continue
            size = path.stat().st_size
            if (not 2 <= size <= CAPTURE_LIMIT or size % 2
                    or metadata.get('sampleRateHz') != SAMPLE_RATE
                    or metadata.get('bytes') != size
                    or metadata.get('format') != 'signed 8-bit interleaved I,Q; no header'):
                continue
            center = metadata.get('frequencyHz')
            validate_config({'frequency_hz': center})
            result.append({'id': path.stem, 'name': path.name, 'frequency_hz': center,
                           'sample_rate': SAMPLE_RATE, 'bytes': size,
                           'duration_seconds': size / 2 / SAMPLE_RATE,
                           'recorded_at': metadata.get('startedUtc'), 'path': path})
        except (OSError, ValueError, TypeError):
            continue
    return result[:30]


class Lab:
    def __init__(self, data_dir=None, tools_dir=None, serial=None, offline=False):
        self.lock = threading.RLock()
        self.commands = threading.Lock()
        self.analysis_lock = threading.RLock()
        self.decode_lock = threading.Lock()
        self.data_dir = Path(data_dir) if data_dir else data_directory()
        self.receiver = Receiver(self.data_dir, tools_dir, serial)
        self.stop_event = threading.Event()
        self.worker = None
        self.processor = None
        self.latest_block = None
        self.ring = deque(maxlen=160)
        self.waterfall = WaterfallHistory()
        self.analyzer = SignalAnalyzer()
        self.temporal = TemporalPatterns()
        self.protocol_decoder = AutomaticDecoders()
        self.protocol_capabilities = self.protocol_decoder.capabilities()
        self.protocol_worker = None
        self.protocol_ring = deque(maxlen=100)
        self.decoder_seen = deque(maxlen=100)
        self.started = time.monotonic()
        self.quitting = False
        self.offline = offline
        self.data = {'application_name': APP_NAME, 'version': VERSION,
                     'mode': 'idle', 'state': 'stopped', 'source_label': 'No source started',
                     'rf_tx_enabled': False, 'config': validate_config({}),
                     'device': {'name': 'HackRF One', 'firmware': None, 'available': False},
                     'rx': {}, 'analysis': None, 'candidates': [], 'events': [],
                     'decoded': [], 'logs': [], 'error': None, 'offline': offline}
        self.data['recognition'] = {'capabilities': self.protocol_capabilities, 'packets': [],
                                    'last_attempt': None, 'error': None}
        self.reset_rx()
        if not offline:
            self.discover()

    def discover(self):
        try:
            info = self.receiver.info()
            self.data['device'] = {'name': info['name'], 'firmware': info['firmware'], 'available': True}
            self.log('HackRF One found. Receiver idle; no transmission is available in this app.')
        except Exception as error:
            self.data['device']['available'] = False
            self.data['device']['error'] = str(error)
            self.log('Radio unavailable; demo and recorded replay remain available.')

    def reset_rx(self):
        self.data['rx'] = {'samples_received': 0, 'bytes_received': 0, 'dropped_analysis_blocks': 0,
                           'elapsed_seconds': 0, 'power_dbfs': None, 'clipped_fraction': None,
                           'last_update': None, 'capture_gaps': 0, 'open_retries': 0}

    def log(self, value):
        with self.lock:
            self.data['logs'].append({'time': utc(), 'message': value})
            self.data['logs'] = self.data['logs'][-80:]

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.data)
            if result['state'] == 'receiving':
                result['rx']['elapsed_seconds'] = round(time.monotonic() - self.started, 1)
        result['captures'] = [{k: v for k, v in capture.items() if k != 'path'}
                              for capture in captures(self.data_dir / 'captures')]
        return result

    def reset_baseline(self):
        with self.analysis_lock:
            self.analyzer.reset()
            self.temporal.reset()
        with self.lock:
            self.data.update(analysis=None, candidates=[], events=[])
        self.log('Baseline reset. New samples must complete warm-up before anomaly scoring.')

    def decoder_config(self, value):
        config = validate_config({'symbol_rate': value}, self.data['config'])
        with self.analysis_lock:
            self.analyzer.symbol_rate = config['symbol_rate']
        with self.lock:
            self.data['config'] = config
        self.log('Candidate bit extraction uses ' + (str(value) + ' symbols/s.' if value else 'no assumed symbol clock.'))

    def _stop(self):
        self.stop_event.set()
        self.receiver.cancel()
        for worker in (self.worker, self.processor, self.protocol_worker):
            if worker and worker is not threading.current_thread():
                worker.join(timeout=12)
                if worker.is_alive():
                    raise RuntimeError('Signal worker is still running; stop remains unconfirmed.')
        self.receiver.close()
        with self.lock:
            self.worker = self.processor = None
            self.protocol_worker = None
            self.latest_block = None
            if self.data['state'] == 'receiving':
                self.data['rx']['elapsed_seconds'] = round(time.monotonic() - self.started, 1)
            self.data.update(state='stopped', rf_tx_enabled=False)

    def stop(self):
        with self.commands:
            self._stop()
            self.log('Receive and analysis stopped. Radio child shutdown confirmed.')

    def start(self, mode, values=None, capture_id='latest'):
        with self.commands:
            if self.quitting:
                raise RuntimeError('App is closing; new source starts are blocked.')
            config = validate_config(values or {}, self.data['config'])
            replay = None
            if mode == 'live' and self.offline:
                raise ValueError('Offline launch cannot access radio hardware.')
            if mode == 'replay':
                choices = captures(self.data_dir / 'captures')
                replay = next((item for item in choices if item['id'] == capture_id), None)
                if capture_id == 'latest' and choices:
                    replay = choices[0]
                if replay is None:
                    raise ValueError('No compatible allowlisted I/Q capture was selected.')
                config['frequency_hz'] = replay['frequency_hz']
            if mode not in ('live', 'demo', 'replay'):
                raise ValueError('Unknown source mode.')
            self._stop()
            if mode == 'live':
                self.discover()
                if not self.data['device']['available']:
                    raise RuntimeError(self.data['device'].get('error', 'HackRF unavailable.'))
                self.receiver.config = config
            with self.analysis_lock:
                self.analyzer.reset()
                self.temporal.reset()
                self.analyzer.symbol_rate = config['symbol_rate']
            with self.lock:
                self.stop_event = threading.Event()
                self.ring.clear()
                self.protocol_ring.clear()
                self.decoder_seen.clear()
                self.waterfall.reset()
                self.latest_block = None
                self.reset_rx()
                source = ('Live HackRF I/Q · receive only' if mode == 'live' else
                          'SYNTHETIC DEMO · generated test signals' if mode == 'demo' else
                          f'Recorded I/Q · {replay["name"]} · {replay["recorded_at"]}')
                self.data.update(mode=mode, state='receiving', config=config, source_label=source,
                                 analysis=None, candidates=[], events=[], decoded=[], error=None)
                self.data['recognition'] = {'capabilities': self.protocol_capabilities, 'packets': [],
                                            'last_attempt': None, 'error': None}
                self.started = time.monotonic()
                self.processor = threading.Thread(target=self._process, daemon=True, name='signal-analysis')
                self.protocol_worker = threading.Thread(target=self._recognize, daemon=True, name='protocol-decoder')
                target = {'live': self._live, 'demo': self._demo, 'replay': self._replay}[mode]
                self.worker = threading.Thread(target=target, args=(replay,) if mode == 'replay' else (),
                                               daemon=True, name='signal-source')
                self.processor.start()
                self.protocol_worker.start()
                self.worker.start()
            self.log('Started ' + source + '. Baseline learning from displayed snapshots.')

    def consume(self, raw):
        if self.stop_event.is_set():
            return -1
        pairs = np.frombuffer(raw, dtype=np.int8).reshape(-1, 2)
        # A narrow center-channel copy for telemetry decoders. This 32-sample
        # averaging filter is inexpensive enough for USB draining. Its alias
        # rejection is limited; it is not an ideal channelizer for the whole band.
        bounded = pairs[:len(pairs) // 32 * 32]
        telemetry = (np.rint(bounded.reshape(-1, 32, 2).mean(axis=1)).astype(np.int8).tobytes()
                     if len(bounded) and self.data['config']['frequency_hz'] < 1_000_000_000 else None)
        with self.lock:
            previous = self.data['rx']['samples_received']
            self.ring.append(pairs[(-previous) % 25::25].copy().tobytes())
            if telemetry:
                self.protocol_ring.append(telemetry)
            self.data['rx']['samples_received'] += len(pairs)
            self.data['rx']['bytes_received'] += len(raw)
            if self.latest_block is not None:
                self.data['rx']['dropped_analysis_blocks'] += 1
            self.latest_block = (raw, utc())
            self.data['rx']['last_update'] = utc()
        return 0

    def _process(self):
        last_decode = 0
        try:
            while not self.stop_event.wait(0.2):
                with self.lock:
                    latest, self.latest_block = self.latest_block, None
                    config = dict(self.data['config'])
                    if latest and len(latest[0]) < self.analyzer.fft_size * 2:
                        # A legitimate final capture fragment can be shorter
                        # than one FFT. Packet buffers already retain its bytes;
                        # skip this display update instead of inventing samples.
                        self.data['rx']['dropped_analysis_blocks'] += 1
                        latest = None
                if latest:
                    raw, timestamp = latest
                    with self.analysis_lock:
                        result = self.analyzer.process_iq(raw, SAMPLE_RATE, config['frequency_hz'], timestamp)
                        iq_pairs = np.frombuffer(raw, dtype=np.int8).reshape(-1, 2).astype(np.float32)
                        iq_values = (iq_pairs[:, 0] + 1j * iq_pairs[:, 1]) / 128
                        result['structure'] = analyze_structure(iq_values, SAMPLE_RATE)
                        if result['baseline'].get('measurement_available', True):
                            result['temporal'] = self.temporal.append(
                                result['spectrum']['power_db'], result['spectrum']['noise_floor_db'],
                                timestamp, SAMPLE_RATE, config['frequency_hz'])
                        else:
                            self.temporal.gap()
                            result['temporal'] = self.temporal.snapshot()
                        events = self.analyzer.events()
                    samples = np.frombuffer(raw, dtype=np.int8).reshape(-1, 2).astype(np.float32)
                    power = float(10 * np.log10(max(float(np.mean(np.sum(samples * samples, axis=1))) / 128**2, 1e-12)))
                    with self.lock:
                        self.data.update(analysis=result, candidates=result['candidates'], events=events)
                        self.data['rx'].update(power_dbfs=round(power, 2),
                                               clipped_fraction=float(np.mean(np.any(
                                                   (samples == -128) | (samples == 127), axis=1))))
                        self.waterfall.append(np.asarray(result['spectrum']['power_db']), timestamp)
                now = time.monotonic()
                if now - last_decode >= 1:
                    last_decode = now
                    self.decode_ring()
        except Exception as error:
            self.fail('Analysis worker: ' + str(error))

    def decode_ring(self):
        with self.decode_lock:
            self._decode_ring_locked()

    def _decode_ring_locked(self):
        with self.lock:
            raw = b''.join(self.ring)
            mode = self.data['mode']
        for payload in decode_iq(raw, sample_rate=DECODE_RATE):
            try:
                packet = decode_packet(payload)
            except (ValueError, UnicodeError):
                continue
            if packet['payload_hex'] in self.decoder_seen:
                continue
            self.decoder_seen.append(packet['payload_hex'])
            with self.lock:
                self.data['decoded'].append({'time': utc(), 'protocol': 'ASDR-LAB v1',
                                            'validation': 'Frame CRC32 and prime-packet CRC32 valid',
                                            'crc_valid': True,
                                            'source_mode': mode, 'source_authenticated': False,
                                            'synthetic': mode == 'demo', 'packet': packet})
                self.data['decoded'][-1]['formats'] = inspect_payload(payload)
                self.data['decoded'] = self.data['decoded'][-40:]
            self.log('Validated ' + ('synthetic ' if mode == 'demo' else '') + 'ASDR-LAB test frame; sender not authenticated.')

    def _recognize(self):
        seen = deque(maxlen=100)
        try:
            while not self.stop_event.wait(1.5):
                with self.lock:
                    raw = b''.join(self.protocol_ring)
                    center = self.data['config']['frequency_hz']
                    mode = self.data['mode']
                records = self.protocol_decoder.decode(raw, 250_000, center)
                capabilities = self.protocol_decoder.capabilities()
                with self.lock:
                    self.data['recognition'].update(capabilities=capabilities,
                        last_attempt=capabilities.get('last_attempt'), error=capabilities.get('last_error'))
                    for item in records:
                        key = json.dumps(item.get('fields', item), sort_keys=True)
                        if key in seen:
                            continue
                        seen.append(key)
                        entry = {**item, 'time': utc(), 'source_mode': mode, 'synthetic': mode == 'demo'}
                        self.data['recognition']['packets'].append(entry)
                    self.data['recognition']['packets'] = self.data['recognition']['packets'][-40:]
        except Exception as error:
            with self.lock:
                self.data['recognition']['error'] = str(error)
            self.log('Optional protocol recognition stopped: ' + str(error))

    def gap(self):
        with self.analysis_lock:
            self.temporal.gap()
        with self.lock:
            self.ring.clear()
            self.protocol_ring.clear()
            self.latest_block = None
            self.waterfall.mark_gap()
            self.data['rx']['capture_gaps'] += 1

    def fail(self, message):
        self.stop_event.set()
        self.receiver.cancel()
        with self.lock:
            self.data['rx']['elapsed_seconds'] = round(time.monotonic() - self.started, 1)
            self.data.update(state='error', error=message)
        self.log(message)

    def _live(self):
        attempts = 0
        try:
            while not self.stop_event.is_set():
                try:
                    self.receiver.receive(self.data['config']['frequency_hz'], self.consume, self.stop_event,
                                          duration=RECEIVE_WINDOW_SECONDS)
                    begin = time.monotonic()
                    while not self.stop_event.wait(0.05):
                        result = self.receiver.poll()
                        if result is not None:
                            if self.receiver.bytes_received != SAMPLE_RATE * RECEIVE_WINDOW_SECONDS * 2:
                                raise RuntimeError('Receiver completed before its finite sample target.')
                            self.decode_ring()
                            break
                        if time.monotonic() - begin > RECEIVE_WINDOW_SECONDS * 2 + 4:
                            raise RuntimeError('Receive child exceeded its finite deadline.')
                    attempts = 0
                except (checked.ReceiveOpenError, checked.ReceiveStallError) as error:
                    attempts += 1
                    self.log(str(error) + f' Receive recovery {attempts}/3.')
                    if attempts >= 3:
                        raise
                    with self.lock:
                        self.data['rx']['open_retries'] += 1
                finally:
                    self.receiver.close()
                if not self.stop_event.is_set():
                    self.gap()
                    self.stop_event.wait(0.5 * attempts if attempts else 0.25)
        except Exception as error:
            self.fail('Receive: ' + str(error))
        finally:
            try:
                self.receiver.close()
            except Exception as error:
                self.fail('Receive shutdown unconfirmed: ' + str(error))

    def _replay(self, capture):
        try:
            while not self.stop_event.is_set():
                with capture['path'].open('rb') as stream:
                    while not self.stop_event.is_set():
                        raw = stream.read(262144)
                        if not raw:
                            break
                        self.consume(raw)
                        self.stop_event.wait(len(raw) / 2 / SAMPLE_RATE)
                if not self.stop_event.is_set():
                    self.decode_ring()
                    self.gap()
                    self.stop_event.wait(0.05)
        except Exception as error:
            self.fail('Recorded replay: ' + str(error))

    def _demo(self):
        rng = np.random.default_rng(20261003)
        frame = 0
        count = 131072
        sample = 0
        demo_packet = encode_iq(build_packet(900001, 5)['payload_text'].encode('ascii'), amplitude=16)
        try:
            while not self.stop_event.is_set():
                time_axis = (np.arange(count, dtype=np.float64) + sample) / SAMPLE_RATE
                scene = frame % 80
                iq = (rng.normal(0, 0.013, count) + 1j * rng.normal(0, 0.013, count))
                if not 60 <= scene < 65:
                    iq += 0.13 * np.exp(2j * np.pi * 450000 * time_axis)
                if 15 <= scene < 55:
                    hop = [-1_450_000, -1_150_000, -850_000][(scene // 5) % 3]
                    envelope = ((np.floor(time_axis * 8000) % 2) == 0).astype(float)
                    iq += 0.21 * envelope * np.exp(2j * np.pi * hop * time_axis)
                if 35 <= scene < 40:
                    iq += 0.30 * np.exp(2j * np.pi * 1_650_000 * time_axis)
                if 60 <= scene < 65:
                    start = (scene - 60) * count * 2
                    piece = demo_packet[start:start + count * 2]
                    if piece:
                        part = np.frombuffer(piece, dtype=np.int8).reshape(-1, 2)
                        iq[:len(part)] += (part[:, 0] + 1j * part[:, 1]) / 128
                interleaved = np.column_stack((np.real(iq), np.imag(iq)))
                raw = np.rint(np.clip(interleaved * 128, -128, 127)).astype(np.int8).tobytes()
                self.consume(raw)
                frame += 1
                sample += count
                self.stop_event.wait(0.2)
        except Exception as error:
            self.fail('Synthetic generator: ' + str(error))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, result, code=200):
        payload = json.dumps(result, allow_nan=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in hosts:
            return self.send_json({'error': 'Local host only.'}, 403)
        target = urlsplit(self.path)
        if target.path == '/api/status':
            return self.send_json(self.server.lab.snapshot())
        if target.path == '/api/waterfall':
            try:
                since = parse_qs(target.query).get('since', ['-1'])
                if len(since) != 1 or len(since[0]) > 20:
                    raise ValueError('Invalid waterfall cursor.')
                lab = self.server.lab
                with lab.lock:
                    result = lab.waterfall.snapshot(int(since[0]), lab.data['config']['frequency_hz'], SAMPLE_RATE)
                return self.send_json(result)
            except (ValueError, TypeError) as error:
                return self.send_json({'error': str(error)}, 400)
        if target.path in ('/', '/index.html'):
            payload = (ROOT / 'index.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if target.path == '/crypto-workbench.js':
            payload = (ROOT / 'crypto-workbench.js').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if target.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return
        self.send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        origin = self.headers.get('Origin')
        if self.headers.get('Host') not in hosts or (origin and origin not in {'http://' + host for host in hosts}):
            return self.send_json({'error': 'Local same-origin control only.'}, 403)
        lab = self.server.lab
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 4096:
                raise ValueError('Request exceeds bounded control size.')
            body = json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('Request must be an object.')
            if self.path == '/api/quit':
                lab.quitting = True
                try:
                    lab.stop()
                except Exception:
                    lab.quitting = False
                    raise
                self.send_json({**lab.snapshot(), 'closed': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if lab.quitting:
                raise RuntimeError('App is closing; control is locked.')
            if self.path == '/api/start':
                lab.start('live', body)
            elif self.path == '/api/demo':
                lab.start('demo')
            elif self.path == '/api/replay':
                lab.start('replay', capture_id=body.get('capture_id', 'latest'))
            elif self.path == '/api/stop':
                lab.stop()
            elif self.path == '/api/baseline/reset':
                lab.reset_baseline()
            elif self.path == '/api/decoder/config':
                lab.decoder_config(body.get('symbol_rate'))
            else:
                return self.send_json({'error': 'Not found'}, 404)
            self.send_json(lab.snapshot())
        except (ValueError, TypeError) as error:
            self.send_json({'error': str(error)}, 400)
        except Exception as error:
            self.send_json({'error': str(error)}, 500)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Anomaly SDR Studio: receive-only raw-IQ analysis.')
    parser.add_argument('--port', type=int, default=8788)
    parser.add_argument('--tools-dir')
    parser.add_argument('--serial')
    parser.add_argument('--data-dir')
    parser.add_argument('--offline', action='store_true', help='Disable all hardware discovery and reception.')
    parser.add_argument('--demo', action='store_true', help='Start clearly labeled synthetic signals.')
    parser.add_argument('--receive', action='store_true', help='Explicitly start receive-only capture.')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or (args.offline and args.receive) or (args.demo and args.receive):
        parser.error('Invalid port or conflicting source modes.')
    lab = Lab(args.data_dir, args.tools_dir, args.serial, args.offline)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.lab = lab
    if args.demo:
        lab.start('demo')
    elif args.receive:
        try:
            lab.start('live')
        except Exception as error:
            lab.data.update(state='error', error=str(error))
    print(f'Anomaly SDR Studio: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        lab.stop()
        server.server_close()


if __name__ == '__main__':
    main()
