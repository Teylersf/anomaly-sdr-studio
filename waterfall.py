"""Bounded measured FFT history for the local receive waterfall."""

import base64
from collections import deque
import uuid

import numpy as np


FFT_SIZE = 8192
FLOOR_DB = -120
CEILING_DB = 0


class WaterfallHistory:
    def __init__(self):
        self.reset()

    def reset(self):
        self.epoch = uuid.uuid4().hex
        self.sequence = 0
        self.rows = deque(maxlen=256)
        self.gap_pending = True

    def mark_gap(self):
        self.gap_pending = True

    def append(self, measured_db, sampled_at):
        # Short captures do not become artificially padded high-resolution rows.
        if len(measured_db) != FFT_SIZE or not np.isfinite(measured_db).all():
            return
        levels = np.rint(np.clip((measured_db - FLOOR_DB) / (CEILING_DB - FLOOR_DB), 0, 1)
                         * 255).astype(np.uint8)
        self.sequence += 1
        self.rows.append({'sequence': self.sequence, 'time': sampled_at,
                          'phase': 'receiving', 'gap_before': self.gap_pending,
                          'power_u8': base64.b64encode(levels.tobytes()).decode('ascii')})
        self.gap_pending = False

    def snapshot(self, since, frequency_hz, sample_rate):
        if isinstance(since, bool) or not isinstance(since, int) or since < -1:
            raise ValueError('Waterfall cursor must be -1 or a nonnegative integer.')
        selected = list(self.rows)[-20:] if since == -1 else [
            row for row in self.rows if row['sequence'] > since][:20]
        return {'epoch': self.epoch, 'latest_sequence': self.sequence,
                'first_sequence': self.rows[0]['sequence'] if self.rows else 0,
                'frequency_hz': frequency_hz, 'sample_rate': sample_rate,
                'fft_size': FFT_SIZE, 'bin_width_hz': sample_rate / FFT_SIZE,
                'floor_db': FLOOR_DB, 'ceiling_db': CEILING_DB,
                'rows': [dict(row) for row in selected]}
