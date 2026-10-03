"""Standalone receive-only adapter to the checked HackRF process lifecycle."""

from pathlib import Path
import subprocess
import threading

from app_config import ASSET_ROOT, data_directory, tools_directory
import radio_backend as checked


class Receiver(checked.Hardware):
    def __init__(self, data_dir, tools_dir=None, serial=None):
        super().__init__()
        checked.BIN = tools_directory(tools_dir)
        checked.RUNTIME = data_directory(data_dir)
        checked.SERIAL = checked.device_serial(serial)
        checked.ROOT = ASSET_ROOT
        self.config = {'lna_gain': 16, 'vga_gain': 16, 'rf_amp': False}

    def receive(self, frequency, callback, stop_event, duration=None):
        # The child is always launched with -r. There is no TX path here.
        command = [str(checked.BIN / 'hackrf_transfer.exe'), '-d', checked.SERIAL, '-r', '-',
                   '-f', str(frequency), '-s', str(checked.SAMPLE_RATE),
                   '-a', '1' if self.config['rf_amp'] else '0', '-p', '0',
                   '-l', str(self.config['lna_gain']), '-g', str(self.config['vga_gain'])]
        if duration is not None:
            command.extend(['-n', str(int(duration * checked.SAMPLE_RATE))])
        with self.launch_lock:
            if stop_event.is_set():
                return
            if self.process is not None:
                raise RuntimeError('A receive process is still owned; overlapping access blocked.')
            self.reader_error = None
            self.bytes_received = 0
            self.expected_bytes = int(duration * checked.SAMPLE_RATE) * 2 if duration else None
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

        def receipts():
            try:
                for line in iter(process.stderr.readline, b''):
                    value = line.decode('utf-8', 'replace').strip()[:1000]
                    self.stderr_tail.append(value)
                    if value == 'Stop with Ctrl-C':
                        self.stream_started.set()
                        self.startup_resolved.set()
            finally:
                self.startup_resolved.set()

        self.reader = threading.Thread(target=consume, daemon=True, name='anomaly-studio-iq')
        self.stderr_reader = threading.Thread(target=receipts, daemon=True, name='anomaly-studio-receipts')
        self.stderr_reader.start()
        self.reader.start()

    def poll(self):
        try:
            return super().poll()
        except RuntimeError as error:
            # A transient USB enumeration gap is recoverable only when the
            # child never started streaming, produced no IQ, exited normally
            # with code 1, and reported this exact startup failure alone.
            receipt = '\n'.join(self.stderr_tail).strip()
            if (self.process is not None and self.process.poll() == 1
                    and not self.stream_started.is_set() and self.bytes_received == 0
                    and self.reader_error is None
                    and receipt == 'hackrf_open() failed: HackRF not found (-5)'):
                raise checked.ReceiveOpenError(receipt) from error
            raise
