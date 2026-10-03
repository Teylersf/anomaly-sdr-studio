"""Longer patterns and gaps in sampled spectral history; no radio or USB."""

import base64
import json
import unittest

import numpy as np

from temporal_patterns import TemporalPatterns


FS = 8_000_000
CENTER = 1_600_000_000


def spectrum(active=False):
    levels = np.full(8192, -90.0)
    if active:
        levels[4600:4603] = -30
    return levels


def matrix(result):
    recurrence = result["recurrence"]
    return np.frombuffer(base64.b64decode(recurrence["similarity_u8"]), np.uint8) \
        .reshape(recurrence["height"], recurrence["width"])


class TemporalPatternTests(unittest.TestCase):
    def test_empty_spectra_are_not_perfect_recurrence(self):
        history = TemporalPatterns()
        for index in range(64):
            result = history.append(spectrum(), -90, 1000 + index * .2, FS, CENTER)
        self.assertFalse(matrix(result).any())
        self.assertFalse(any(result["occupancy"]["duty_cycle"]))
        self.assertEqual(result["periodicity"]["state"], "no-repeat-peak")

    def test_seeded_gaussian_floor_has_no_active_bands_or_repeat(self):
        rng = np.random.default_rng(71)
        history = TemporalPatterns()
        for index in range(64):
            levels = -90 + rng.normal(0, .8, 8192)
            result = history.append(levels, -90, 1000 + index * .2, FS, CENTER)
        self.assertFalse(matrix(result).any())
        self.assertNotEqual(result["periodicity"]["state"], "candidate")

    def test_persistent_carrier_recurs_without_power_repetition(self):
        history = TemporalPatterns()
        for index in range(40):
            result = history.append(spectrum(True), -90, 1000 + index * .2, FS, CENTER)
        self.assertTrue((matrix(result) == 255).all())
        self.assertEqual(max(result["occupancy"]["duty_cycle"]), 1)
        self.assertEqual(result["periodicity"]["state"], "no-repeat-peak")

    def test_repeating_bursts_have_candidate_period_and_observed_duty(self):
        history = TemporalPatterns()
        for index in range(80):
            result = history.append(spectrum(index % 10 < 5), -90, 1000 + index * .2, FS, CENTER)
        self.assertEqual(result["periodicity"]["state"], "candidate")
        self.assertAlmostEqual(result["periodicity"]["peak_seconds"], 2, places=6)
        self.assertEqual(max(result["occupancy"]["duty_cycle"]), .5)
        self.assertEqual(matrix(result)[0, 10], 255)
        self.assertEqual(matrix(result)[0, 5], 0)
        self.assertEqual(matrix(result)[5, 15], 0)

    def test_gap_does_not_join_two_short_runs_into_periodic_evidence(self):
        history = TemporalPatterns()
        for index in range(20):
            history.append(spectrum(index % 10 < 5), -90, 1000 + index * .2, FS, CENTER)
        history.gap()
        for index in range(20):
            result = history.append(spectrum(index % 10 < 5), -90, 1020 + index * .2, FS, CENTER)
        self.assertEqual(result["occupancy"]["frames"], 40)
        self.assertEqual(result["periodicity"]["state"], "insufficient")
        self.assertTrue(result["recurrence"]["gap_rows"][20])
        self.assertEqual(matrix(result)[0, 20], 255)

    def test_unannounced_large_time_jump_marks_a_gap(self):
        history = TemporalPatterns()
        for index in range(35):
            history.append(spectrum(index % 10 < 5), -90, 1000 + index * .2, FS, CENTER)
        result = history.append(spectrum(True), -90, 1100, FS, CENTER)
        self.assertTrue(result["recurrence"]["gap_rows"][-1])
        self.assertEqual(result["periodicity"]["state"], "insufficient")

    def test_irregular_timing_is_not_resampled_to_manufacture_a_period(self):
        history = TemporalPatterns()
        stamp = 1000.0
        for index in range(40):
            stamp += .2 if index % 2 else .35
            result = history.append(spectrum(index % 10 < 5), -90, stamp, FS, CENTER)
        self.assertEqual(result["periodicity"]["state"], "insufficient")
        self.assertIn("not resampled", result["periodicity"]["evidence"])

    def test_retune_clears_old_frequency_history(self):
        history = TemporalPatterns()
        history.append(spectrum(True), -90, 1000, FS, CENTER)
        result = history.append(spectrum(), -90, 1001, FS, CENTER + 10_000_000)
        self.assertEqual(result["occupancy"]["frames"], 1)
        self.assertFalse(matrix(result).any())
        self.assertTrue(result["recurrence"]["gap_rows"][0])

    def test_center_dc_and_band_edges_do_not_become_active_columns(self):
        levels = spectrum()
        levels[4093:4100] = 0
        levels[:2] = 0
        levels[-2:] = 0
        result = TemporalPatterns().append(levels, -90, 1000, FS, CENTER)
        self.assertFalse(matrix(result).any())

    def test_bounded_history_valid_json_and_iso_timestamps(self):
        history = TemporalPatterns(max_frames=6, columns=128)
        for index in range(20):
            result = history.append(spectrum(index % 2 == 0), -90, 1000 + index * .2, FS, CENTER)
        self.assertEqual(result["occupancy"]["frames"], 6)
        self.assertEqual(matrix(result).shape, (6, 6))
        self.assertEqual(len(result["occupancy"]["frequencies_hz"]), 128)
        self.assertTrue(result["power_timeline"]["times"][-1].endswith("Z"))
        json.dumps(result, allow_nan=False)
        history.reset()
        empty = history.snapshot()
        self.assertEqual(empty["occupancy"]["frames"], 0)
        self.assertEqual(empty["recurrence"]["similarity_u8"], "")

    def test_nonpositive_clock_step_is_an_explicit_gap(self):
        history = TemporalPatterns()
        history.append(spectrum(True), -90, "2026-10-03T10:00:00Z", FS, CENTER)
        result = history.append(spectrum(True), -90, "2026-10-03T09:00:00Z", FS, CENTER)
        self.assertTrue(result["recurrence"]["gap_rows"][-1])
        self.assertGreaterEqual(result["occupancy"]["span_seconds"], 0)

    def test_invalid_values_are_rejected(self):
        history = TemporalPatterns()
        for levels in (np.zeros(8191), np.full(8192, float("nan"))):
            with self.assertRaises(ValueError):
                history.append(levels, -90, 1000, FS, CENTER)
        with self.assertRaises(ValueError):
            history.append(spectrum(), -90, "not a time", FS, CENTER)
        with self.assertRaises(ValueError):
            history.append(spectrum(), -90, 1000, 0, CENTER)


if __name__ == "__main__":
    unittest.main()
