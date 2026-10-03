"""Standalone checked receive-only HackRF subprocess lifecycle."""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from collections import deque

from app_config import ASSET_ROOT, data_directory, tools_directory, device_serial

ROOT = ASSET_ROOT
BIN = tools_directory()
RUNTIME = data_directory()
SERIAL = None
SAMPLE_RATE = 8_000_000


def native_rf_off_receipt(returncode, output, mode, interrupted=False, allow_idle_warning=False):
    """Validate checked native stop/close operations, separately from capture success."""
    if interrupted or mode != 'rx':
        return False
    receipt = output.decode('utf-8', 'replace') if isinstance(output, bytes) else str(output)
    if returncode != 0 and not (returncode == 1 and mode == 'rx' and allow_idle_warning
                              and "Couldn't transfer any bytes for one second." in receipt):
        return False
    if 'failed' in receipt.lower() or 'caught signal' in receipt.lower():
        return False
    lines = [line.strip() for line in receipt.splitlines() if line.strip()]
    markers = (f'hackrf_stop_{mode}() done', 'hackrf_close() done', 'hackrf_exit() done')
    if not lines or lines[-1] != 'exit' or any(marker not in lines for marker in markers):
        return False
    positions = [lines.index(marker) for marker in markers]
    return positions == sorted(positions)


class ReceiveOpenError(RuntimeError):
    """A failed USB claim before the receiver started; no IQ was accepted."""


class ReceiveStallError(RuntimeError):
    """The native idle timer ended an incomplete, cleanly closed RX window."""

    def __init__(self, received_bytes, expected_bytes, receipt):
        self.received_bytes = received_bytes
        self.expected_bytes = expected_bytes
        self.receipt = receipt
        super().__init__(f'Receive transfer stalled after {received_bytes // 2:,} of '
                         f'{expected_bytes // 2:,} requested samples.')


class Hardware:
    """Keep every native HackRF operation outside the dashboard process."""

    def __init__(self):
        self.launch_lock = threading.RLock()
        self.process = None
        self.reader = None
        self.stderr_reader = None
        self.reader_error = None
        self.bytes_received = 0
        self.expected_bytes = None
        self.completion_warning = None
        self.interrupted = False
        self.stream_started = threading.Event()
        self.startup_resolved = threading.Event()
        self.stderr_tail = deque(maxlen=24)

    @staticmethod
    def environment():
        environment = os.environ.copy()
        environment['PATH'] = str(BIN) + os.pathsep + environment.get('PATH', '')
        return environment

    def _control(self, idle=False):
        command = ([sys.executable, '--radio-control'] if getattr(sys, 'frozen', False)
                   else [sys.executable, str(ROOT / 'radio_control.py')])
        command.extend(['--tools-dir', str(BIN)])
        if SERIAL:
            command.extend(['--serial', SERIAL])
        if idle:
            command.append('--idle')
        result = subprocess.run(command, capture_output=True, timeout=8,
                                env=self.environment(),
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode != 0:
            detail = (result.stdout + result.stderr).decode('utf-8', 'replace')[-1000:]
            raise RuntimeError(f'Radio control child exited {result.returncode}: {detail}')
        return json.loads(result.stdout)

    def info(self):
        global SERIAL
        answer = self._control()
        SERIAL = device_serial(answer['serial'])
        return answer

    def receive(self, frequency, callback, stop_event, duration=None):
        command = [str(BIN / 'hackrf_transfer.exe'), '-d', SERIAL, '-r', '-',
                   '-f', str(frequency), '-s', str(SAMPLE_RATE),
                   '-a', '0', '-p', '0', '-l', '16', '-g', '16']
        if duration is not None:
            command.extend(['-n', str(int(duration * SAMPLE_RATE))])
        with self.launch_lock:
            if stop_event.is_set():
                return
            if self.process is not None:
                raise RuntimeError('A receive process is still owned; overlapping radio access blocked.')
            self.reader_error = None
            self.bytes_received = 0
            self.expected_bytes = int(duration * SAMPLE_RATE) * 2 if duration is not None else None
            self.completion_warning = None
            self.interrupted = False
            self.stream_started.clear()
            self.startup_resolved.clear()
            self.stderr_tail.clear()
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env=self.environment(),
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.process = process

        def consume():
            try:
                while True:
                    raw = process.stdout.read(262144)
                    if not raw:
                        break
                    # Failed startup prints usage() to stdout in this tool version.
                    # It is not IQ. Only the stderr startup receipt opens this gate.
                    if not self.startup_resolved.wait(timeout=3):
                        raise RuntimeError('Receive child did not confirm startup.')
                    if not self.stream_started.is_set():
                        continue
                    if stop_event.is_set():
                        break
                    if len(raw) % 2:
                        raise RuntimeError('Receive child returned a partial I/Q sample.')
                    self.bytes_received += len(raw)
                    if callback(raw) != 0:
                        break
            except Exception as error:
                self.reader_error = str(error)
                if process.poll() is None:
                    self.interrupted = True
                    process.terminate()

        def consume_stderr():
            try:
                for line in iter(process.stderr.readline, b''):
                    text = line.decode('utf-8', 'replace').strip()[:1000]
                    self.stderr_tail.append(text)
                    if text == 'Stop with Ctrl-C':
                        self.stream_started.set()
                        self.startup_resolved.set()
            finally:
                self.startup_resolved.set()

        self.reader = threading.Thread(target=consume, daemon=True, name='hackrf-iq-pipe')
        self.stderr_reader = threading.Thread(target=consume_stderr, daemon=True, name='hackrf-receipt-pipe')
        self.stderr_reader.start()
        self.reader.start()

    def poll(self):
        if self.reader_error:
            raise RuntimeError(f'Receive processing: {self.reader_error}')
        if self.process is None:
            return 0
        result = self.process.poll()
        if result is not None and any(reader and reader.is_alive()
                                      for reader in (self.reader, self.stderr_reader)):
            return None
        if result not in (None, 0):
            receipt = '\n'.join(self.stderr_tail)
            open_denied = 'hackrf_open() failed: Access denied (insufficient permissions) (-1000)'
            if (result == 1 and not self.stream_started.is_set()
                    and self.bytes_received == 0 and receipt.strip() == open_denied):
                raise ReceiveOpenError(receipt)
            # 2024.02.1's timer checks byte_count==0 after the RX callback can
            # already have set do_exit on its sample limit (transfer.c:1367).
            # Accept only that specific diagnostic with the exact finite data
            # target and all normal shutdown receipts, never a partial capture.
            clean_timer_stop = (
                result == 1 and self.stream_started.is_set()
                and self.expected_bytes is not None
                and "Couldn't transfer any bytes for one second." in receipt
                and all(marker in receipt for marker in
                        ('hackrf_stop_rx() done', 'hackrf_close() done', 'hackrf_exit() done'))
                and 'failed' not in receipt.lower())
            if clean_timer_stop and self.bytes_received == self.expected_bytes:
                self.completion_warning = 'Finite RX target complete; native timer idle warning with normal shutdown.'
                return 0
            if clean_timer_stop and self.bytes_received < self.expected_bytes:
                raise ReceiveStallError(self.bytes_received, self.expected_bytes, receipt)
            raise RuntimeError(f'Receive child exited {result}: ' + receipt)
        return result

    def cancel(self):
        with self.launch_lock:
            if self.process is not None and self.process.poll() is None:
                self.interrupted = True
                self.process.terminate()

    def close(self):
        with self.launch_lock:
            process = self.process
        if process is None:
            return
        reaped = False
        readers_stopped = False
        try:
            if process.poll() is None:
                self.interrupted = True
                process.terminate()
            try:
                returncode = process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.interrupted = True
                process.kill()
                returncode = process.wait(timeout=3)
            reaped = True
            for reader in (self.reader, self.stderr_reader):
                if reader:
                    reader.join(timeout=2)
                    if reader.is_alive():
                        raise RuntimeError('Receive pipe reader has not stopped.')
            process.stdout.close()
            process.stderr.close()
            readers_stopped = True
            runtime = RUNTIME
            runtime.mkdir(parents=True, exist_ok=True)
            (runtime / 'last-receive.log').write_text('\n'.join(self.stderr_tail), encoding='utf-8')
        finally:
            if reaped:
                if readers_stopped:
                    with self.launch_lock:
                        self.process = None
                receipt = '\n'.join(self.stderr_tail)
                if not (readers_stopped and native_rf_off_receipt(
                        returncode, receipt, 'rx', interrupted=self.interrupted,
                        allow_idle_warning=self.stream_started.is_set())):
                    self._control(idle=True)

    def force_idle(self):
        # v2024.02.1 hackrf_close sends mode OFF even on a freshly opened handle.
        # A fresh-handle close sends mode OFF before releasing the device.
        if self.process is not None:
            self.close()
        self._control(idle=True)
