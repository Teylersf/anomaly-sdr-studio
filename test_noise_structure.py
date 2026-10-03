"""Known waveform contrasts for the descriptive structure detector; no radio."""

import json
import math
import unittest

import numpy as np

from noise_structure import analyze_structure, MAX_SAMPLES


FS = 1_000_000
N = MAX_SAMPLES


def noise(seed=7, count=N):
    rng = np.random.default_rng(seed)
    return .15 * (rng.normal(size=count) + 1j * rng.normal(size=count))


class NoiseStructureTests(unittest.TestCase):
    def test_independent_gaussian_reference_is_low(self):
        for seed in (2, 7, 43, 113):
            with self.subTest(seed=seed):
                result = analyze_structure(noise(seed), FS)
                self.assertLess(result["structure_score"], 20)
                self.assertGreater(result["metrics"]["spectral_flatness"], .75)
                self.assertGreater(result["metrics"]["normalized_spectral_entropy"], .95)
                self.assertFalse(any(flag["strength"] > .5 for flag in result["flags"]))

    def test_stable_carrier_is_structured_without_a_learned_baseline(self):
        carrier = .3 * np.exp(2j * np.pi * 53_000 * np.arange(N) / FS)
        first = analyze_structure(carrier, FS)
        again = analyze_structure(carrier, FS)
        self.assertGreater(first["structure_score"], 85)
        self.assertEqual(first, again)
        self.assertLess(first["metrics"]["normalized_spectral_entropy"], .3)

    def test_colored_gaussian_is_dependence_not_a_message(self):
        source = noise(11)
        colored = np.empty(N, complex)
        colored[0] = source[0]
        for index in range(1, N):
            colored[index] = .985 * colored[index - 1] + source[index]
        colored *= .3 / np.max(np.abs(colored))
        result = analyze_structure(colored, FS)
        self.assertGreater(result["structure_score"], 70)
        self.assertIn("Correlated / colored signal", [flag["label"] for flag in result["flags"]])
        self.assertNotIn("communication detected", json.dumps(result).lower())

    def test_repeating_pulses_reveal_envelope_rhythm(self):
        time = np.arange(N)
        envelope = np.where(time % 256 < 64, .3, .015)
        data = envelope * np.exp(2j * np.pi * 81_000 * time / FS)
        result = analyze_structure(data, FS)
        self.assertGreater(result["structure_score"], 60)
        self.assertGreater(result["metrics"]["periodicity_peak"], .15)
        self.assertAlmostEqual(result["metrics"]["periodicity_hz"], FS / 256, delta=FS / N)
        self.assertIn("Repeated envelope", [flag["label"] for flag in result["flags"]])

    def test_amplitude_modulation_has_a_periodic_envelope(self):
        time = np.arange(N) / FS
        data = (.2 + .1 * np.sin(2 * np.pi * 8_000 * time)) * np.exp(2j * np.pi * 90_000 * time)
        result = analyze_structure(data, FS)
        self.assertGreater(result["metrics"]["periodicity_peak"], .8)
        self.assertAlmostEqual(result["metrics"]["periodicity_hz"], 8_000, delta=FS / N)

    def test_random_phase_with_constant_envelope_is_not_gaussian_noise(self):
        rng = np.random.default_rng(5)
        data = .3 * np.exp(1j * rng.uniform(-np.pi, np.pi, N))
        result = analyze_structure(data, FS)
        self.assertGreater(result["structure_score"], 50)
        self.assertGreater(result["metrics"]["spectral_flatness"], .75)
        self.assertIn("Constant envelope", [flag["label"] for flag in result["flags"]])
        self.assertFalse(result["metrics"]["amplitude_kurtosis_available"])

    def test_clipped_noise_warns_about_digital_full_scale(self):
        data = noise(14) * 6
        clipped = np.clip(data.real, -1, 1) + 1j * np.clip(data.imag, -1, 1)
        result = analyze_structure(clipped, FS)
        self.assertGreater(result["metrics"]["clipped_percent"], 10)
        self.assertIn("Near digital full scale", [flag["label"] for flag in result["flags"]])

    def test_real_only_noise_exposes_quadrature_imbalance(self):
        result = analyze_structure(noise(16).real.astype(complex), FS)
        self.assertGreater(result["metrics"]["circularity_coefficient"], .99)
        self.assertIn("I/Q dependence or imbalance", [flag["label"] for flag in result["flags"]])

    def test_alternating_signs_are_not_independent_runs(self):
        data = .2 * (-1.0) ** np.arange(N) + 1j * noise(3).imag
        result = analyze_structure(data, FS)
        self.assertGreater(abs(result["metrics"]["sign_runs_z"]), 30)
        self.assertIn("Unusual sign runs", [flag["label"] for flag in result["flags"]])

    def test_zero_and_dc_only_inputs_are_finite_labeled_flatlines(self):
        for value in (0j, .2 + .1j):
            with self.subTest(value=value):
                result = analyze_structure(np.full(N, value, complex), FS)
                self.assertEqual(result["state"], "constant")
                self.assertEqual(result["flags"][0]["label"], "Flatline / constant samples")
                json.dumps(result, allow_nan=False)

    def test_empty_and_short_capture_need_more_samples(self):
        for count in (0, 10, 63):
            result = analyze_structure(np.zeros(count, complex), FS)
            self.assertEqual(result["state"], "insufficient-data")
            self.assertEqual(result["structure_score"], 0)
            json.dumps(result, allow_nan=False)

    def test_analysis_uses_the_contiguous_tail_and_bounded_arrays(self):
        data = np.concatenate((np.full(N * 7, .2 + .1j), noise(77)))
        result = analyze_structure(data, FS)
        self.assertEqual(result, analyze_structure(data[-N:], FS))
        self.assertEqual(result["samples_analyzed"], N)
        self.assertLessEqual(len(result["autocorrelation"]["values"]), 512)
        self.assertLessEqual(len(result["periodicity_spectrum"]["power_relative"]), 256)
        json.dumps(result, allow_nan=False)

    def test_invalid_inputs_are_rejected(self):
        for bad in (np.array([1.0, 2.0]), np.zeros((2, 2), complex),
                    np.array([complex(math.inf, 0)])):
            with self.assertRaises(ValueError):
                analyze_structure(bad, FS)
        for bad_rate in (0, -1, math.nan, True):
            with self.assertRaises(ValueError):
                analyze_structure(noise(), bad_rate)


if __name__ == "__main__":
    unittest.main()
