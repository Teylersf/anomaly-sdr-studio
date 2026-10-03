"""Offline measured-waterfall tests; no sockets, USB, or RF processes."""

import base64
import unittest

import numpy as np

from waterfall import CEILING_DB, FFT_SIZE, FLOOR_DB, WaterfallHistory


def decode_row(row):
    return np.frombuffer(base64.b64decode(row['power_u8'], validate=True), dtype=np.uint8)


def measured_row(value=-60):
    return np.full(FFT_SIZE, value, dtype=np.float32)


class WaterfallHistoryTests(unittest.TestCase):
    def setUp(self):
        self.history = WaterfallHistory()

    def snapshot(self, since=-1):
        return self.history.snapshot(since, 1_600_000_000, 8_000_000)

    def test_measured_row_decodes_to_8192_bins_with_declared_power_scale(self):
        measured = np.linspace(-140, 20, FFT_SIZE, dtype=np.float32)
        self.history.append(measured, '2026-10-03T12:00:00.000Z')
        snapshot = self.snapshot()
        levels = decode_row(snapshot['rows'][0])
        self.assertEqual(levels.size, 8192)
        self.assertEqual(levels[0], 0)
        self.assertEqual(levels[-1], 255)
        decoded_db = snapshot['floor_db'] + levels.astype(float) / 255 * (
            snapshot['ceiling_db'] - snapshot['floor_db'])
        quantization_half_step = (CEILING_DB - FLOOR_DB) / 255 / 2
        self.assertLessEqual(float(np.max(np.abs(decoded_db - np.clip(measured, FLOOR_DB, CEILING_DB)))),
                             quantization_half_step + 0.00001)
        self.assertEqual(snapshot['rows'][0]['time'], '2026-10-03T12:00:00.000Z')
        self.assertEqual(snapshot['rows'][0]['phase'], 'receiving')

    def test_short_or_nonfinite_input_never_adds_a_fake_row(self):
        invalid_rows = (np.zeros(256), np.zeros(4096), np.zeros(FFT_SIZE + 1),
                        np.full(FFT_SIZE, np.nan), np.full(FFT_SIZE, np.inf))
        for invalid in invalid_rows:
            with self.subTest(size=len(invalid), first=invalid[0]):
                self.history.append(invalid, 'invalid')
                self.assertEqual(self.snapshot()['rows'], [])
                self.assertEqual(self.snapshot()['latest_sequence'], 0)
        self.history.append(measured_row(), 'valid')
        self.assertTrue(self.snapshot()['rows'][0]['gap_before'])

    def test_history_and_incremental_batches_are_bounded_without_repeated_rows(self):
        row = measured_row()
        for number in range(1, 301):
            self.history.append(row, f'frame-{number}')
        latest = self.snapshot()
        self.assertEqual(len(self.history.rows), 256)
        self.assertEqual(latest['first_sequence'], 45)
        self.assertEqual(latest['latest_sequence'], 300)
        self.assertEqual([row['sequence'] for row in latest['rows']], list(range(281, 301)))

        cursor = 44
        recovered = []
        while cursor < 300:
            snapshot = self.snapshot(cursor)
            self.assertLessEqual(len(snapshot['rows']), 20)
            sequences = [row['sequence'] for row in snapshot['rows']]
            self.assertTrue(sequences)
            self.assertEqual(sequences[0], cursor + 1)
            recovered.extend(sequences)
            cursor = sequences[-1]
        self.assertEqual(recovered, list(range(45, 301)))
        self.assertEqual(self.snapshot(300)['rows'], [])
        self.assertEqual(self.snapshot(301)['rows'], [])
        self.assertEqual([row['sequence'] for row in self.snapshot(0)['rows']], list(range(45, 65)))

    def test_snapshots_cannot_mutate_stored_rows_or_history_metadata(self):
        self.history.append(measured_row(), 'original-time')
        snapshot = self.snapshot()
        original = self.snapshot()
        snapshot['epoch'] = 'changed'
        snapshot['frequency_hz'] = 0
        snapshot['rows'][0]['time'] = 'changed'
        snapshot['rows'][0]['power_u8'] = ''
        snapshot['rows'].clear()
        self.assertEqual(self.snapshot(), original)

    def test_frequency_and_sample_rate_describe_exact_fft_bin_spacing(self):
        snapshot = self.history.snapshot(-1, 1_420_000_000, 8_000_000)
        self.assertEqual(snapshot['frequency_hz'], 1_420_000_000)
        self.assertEqual(snapshot['sample_rate'], 8_000_000)
        self.assertEqual(snapshot['fft_size'], 8192)
        self.assertEqual(snapshot['bin_width_hz'], 976.5625)

    def test_reset_changes_epoch_and_clears_sequence_and_rows(self):
        self.history.append(measured_row(), 'old')
        old_epoch = self.snapshot()['epoch']
        self.history.reset()
        empty = self.snapshot()
        self.assertNotEqual(empty['epoch'], old_epoch)
        self.assertEqual(empty['latest_sequence'], 0)
        self.assertEqual(empty['first_sequence'], 0)
        self.assertEqual(empty['rows'], [])
        self.history.append(measured_row(), 'new')
        row = self.snapshot()['rows'][0]
        self.assertEqual(row['sequence'], 1)
        self.assertTrue(row['gap_before'])

    def test_gap_marker_belongs_only_to_next_measured_row(self):
        self.history.append(measured_row(), 'first')
        self.history.append(measured_row(), 'second')
        self.history.mark_gap()
        self.history.mark_gap()
        self.history.append(np.zeros(256), 'short')
        self.assertEqual(self.snapshot()['latest_sequence'], 2)
        self.history.append(measured_row(), 'after-gap')
        self.history.append(measured_row(), 'following')
        self.assertEqual([row['gap_before'] for row in self.snapshot()['rows']],
                         [True, False, True, False])
        self.assertEqual(self.snapshot(4)['rows'], [])
        self.assertFalse(self.history.gap_pending)

    def test_invalid_typed_cursors_are_rejected(self):
        for cursor in (True, False, -2, -100, 1.5, '1', None):
            with self.subTest(cursor=cursor):
                with self.assertRaisesRegex(ValueError, 'cursor'):
                    self.snapshot(cursor)


if __name__ == "__main__":
    unittest.main()
