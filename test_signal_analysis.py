"""Synthetic evidence tests: no radio, network, hardware, or transmitted RF."""

import base64
import json
import math
import unittest

import numpy as np

from signal_analysis import MAX_EVENTS, MAX_SAMPLES, SignalAnalyzer


RATE = 1_000_000
CENTER = 1_600_000_000
N = 65_536


def noise(seed, n=N, amplitude=.0004):
    rng = np.random.default_rng(seed)
    return amplitude * (rng.normal(size=n) + 1j * rng.normal(size=n))


def tone(offset=125_000, amplitude=.15, n=N):
    return amplitude * np.exp(2j * np.pi * offset * np.arange(n) / RATE)


class SignalAnalysisTests(unittest.TestCase):
    def analyze(self, iq, analyzer=None, **kwargs):
        return (analyzer or SignalAnalyzer()).process_iq(iq, RATE, CENTER, "2026-10-03T12:00:00Z", **kwargs)

    def test_tone_frequency_and_occupied_bandwidth(self):
        result = self.analyze(tone(125_000) + noise(1))
        feature = result["candidates"][0]
        self.assertAlmostEqual(feature["offset_hz"], 125_000, delta=RATE / 8192)
        self.assertLess(feature["bandwidth_hz"], 1_000)
        self.assertGreater(feature["snr_db"], 40)
        self.assertIn("narrowband", feature["tags"])
        self.assertEqual(result["spectrum"]["fft_size"], 8192)

    def test_negative_baseband_frequency_is_preserved(self):
        result = self.analyze(tone(-220_000) + noise(2))
        self.assertAlmostEqual(result["candidates"][0]["offset_hz"], -220_000, delta=200)

    def test_noise_is_not_identified_as_a_message_or_carrier(self):
        result = self.analyze(noise(3, amplitude=.02))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["demodulation"]["bits"]["state"], "no-candidate")
        self.assertFalse(result["demodulation"]["bits"]["verified"])

    def test_single_fft_noise_uses_a_conservative_detection_threshold(self):
        result = self.analyze(noise(7, n=8192, amplitude=.02))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["spectrum"]["detection_margin_db"], 21)
        self.assertEqual(result["spectrum"]["averaged_fft_frames"], 1)

    def test_zero_input_does_not_teach_a_false_baseline_or_noise_level(self):
        result = self.analyze(np.zeros(N, dtype=complex))
        self.assertFalse(result["spectrum"]["noise_floor_available"])
        self.assertFalse(result["baseline"]["measurement_available"])
        self.assertEqual(result["baseline"]["frames"], 0)
        self.assertEqual(result["candidates"], [])
        json.dumps(result, allow_nan=False)

    def test_receiver_dc_does_not_become_a_detected_signal(self):
        result = self.analyze(np.ones(N, dtype=complex) * .25)
        self.assertEqual(result["candidates"], [])
        self.assertTrue(result["spectrum"]["dc_removed"])
        result = self.analyze(np.ones(N, dtype=complex) * .25 + noise(4))
        self.assertEqual(result["candidates"], [])

    def test_wideband_feature_is_segmented_with_measured_width(self):
        rng = np.random.default_rng(5)
        bins = np.fft.fftfreq(N, 1 / RATE)
        frequency_data = np.zeros(N, dtype=complex)
        selected = (bins > 80_000) & (bins < 150_000)
        frequency_data[selected] = rng.normal(size=selected.sum()) + 1j * rng.normal(size=selected.sum())
        data = np.fft.ifft(frequency_data)
        data = data / np.sqrt(np.mean(np.abs(data) ** 2)) * .05 + noise(5)
        result = self.analyze(data)
        candidate = result["candidates"][0]
        self.assertIn("wideband", candidate["tags"])
        self.assertAlmostEqual(candidate["offset_hz"], 115_000, delta=5_000)
        self.assertGreater(candidate["bandwidth_hz"], 60_000)
        self.assertLess(candidate["bandwidth_hz"], 85_000)

    def test_cold_start_suppresses_anomaly_claims(self):
        analyzer = SignalAnalyzer()
        for index in range(8):
            result = self.analyze(tone() + noise(index), analyzer)
            self.assertTrue(result["baseline"]["cold_start"])
            self.assertEqual(result["baseline"]["anomaly_score"], 0)
            self.assertEqual(result["events"], [])
        result = self.analyze(tone() + noise(9), analyzer)
        self.assertEqual(result["baseline"]["state"], "ready")
        self.assertFalse(result["baseline"]["cold_start"])
        self.assertLess(result["candidates"][0]["anomaly_score"], 10)

    def test_new_tone_after_baseline_is_statistical_novelty(self):
        analyzer = SignalAnalyzer()
        for index in range(8):
            self.analyze(noise(index), analyzer)
        result = self.analyze(tone() + noise(20), analyzer)
        self.assertGreater(result["baseline"]["anomaly_score"], 60)
        self.assertGreater(result["candidates"][0]["anomaly_score"], 60)
        event = result["events"][-1]
        self.assertEqual(event["type"], "spectral-novelty")
        self.assertIn("origin unknown", event["label"])
        self.assertGreater(event["evidence"]["band_z_90"], 4)
        # Staying continuously above the alert threshold does not flood events.
        next_result = self.analyze(tone() + noise(21), analyzer)
        self.assertEqual(len(next_result["events"]), 1)

    def test_persistent_burst_and_possible_hop_tags_are_grounded(self):
        analyzer = SignalAnalyzer()
        for index in range(3):
            result = self.analyze(tone() + noise(index), analyzer)
        self.assertIn("persistent", result["candidates"][0]["tags"])
        self.assertEqual(result["candidates"][0]["persistence_frames"], 3)
        identifier = result["candidates"][0]["id"]
        self.analyze(noise(10), analyzer)
        result = self.analyze(tone() + noise(11), analyzer)
        self.assertEqual(result["candidates"][0]["id"], identifier)
        self.assertIn("burst-reappearance", result["candidates"][0]["tags"])
        result = self.analyze(tone(210_000) + noise(12), analyzer)
        self.assertIn("possible-frequency-hop", result["candidates"][0]["tags"])
        self.assertFalse(result["candidates"][0]["hop_evidence"]["association_verified"])

    def test_re_tuning_resets_baseline_tracks_and_events(self):
        analyzer = SignalAnalyzer(warmup_frames=2)
        self.analyze(noise(1), analyzer)
        self.analyze(noise(2), analyzer)
        self.analyze(tone() + noise(3), analyzer)
        self.assertTrue(analyzer.events())
        result = analyzer.process_iq(tone(), RATE, CENTER + RATE, "retuned")
        self.assertEqual(result["sequence"], 1)
        self.assertEqual(result["baseline"]["frames"], 1)
        self.assertTrue(result["baseline"]["cold_start"])
        self.assertEqual(result["events"], [])
        analyzer.reset()
        self.assertIsNone(analyzer.latest())

    def test_visual_arrays_are_bounded_and_have_correct_units(self):
        result = self.analyze(tone(125_000) + noise(6))
        visuals = result["visuals"]
        self.assertLessEqual(len(visuals["constellation"]["points"]), 1024)
        self.assertEqual(sum(visuals["amplitude_histogram"]["counts"]), N)
        self.assertEqual(sum(visuals["phase_increments"]["histogram"]["counts"]), N - 1)
        self.assertAlmostEqual(np.median(visuals["phase_increments"]["instantaneous_hz"]), 125_000, delta=100)
        spectrogram = visuals["spectrogram"]
        self.assertEqual(len(base64.b64decode(spectrogram["power_u8"])), spectrogram["width"] * spectrogram["height"])
        self.assertLessEqual(spectrogram["height"], 32)
        self.assertAlmostEqual(np.median(spectrogram["ridge_hz"]), CENTER + 125_000, delta=200)
        self.assertEqual(len(base64.b64decode(result["spectrum"]["waterfall_u8"])), 8192)
        self.assertLess(len(json.dumps(result, allow_nan=False)), 220_000)

    def test_signed_hackrf_int8_iq_and_clipping_are_measured(self):
        data = tone(amplitude=.2)
        raw = np.column_stack((np.rint(data.real * 128), np.rint(data.imag * 128))).astype(np.int8)
        result = self.analyze(raw.tobytes())
        self.assertAlmostEqual(result["candidates"][0]["offset_hz"], 125_000, delta=200)
        self.assertAlmostEqual(result["metrics"]["power_dbfs"], 10 * math.log10(.04), delta=.4)
        raw[0] = [127, -128]
        result = self.analyze(raw.tobytes())
        self.assertGreater(result["metrics"]["clipped_percent"], 0)

    def test_large_capture_processing_is_capped(self):
        data = np.zeros(MAX_SAMPLES + 8192, dtype=complex)
        data[-N:] = tone()
        result = self.analyze(data)
        self.assertEqual(result["samples_analyzed"], MAX_SAMPLES)
        self.assertTrue(result["input_truncated"])

    def test_no_bits_are_extracted_without_an_explicit_symbol_clock(self):
        result = self.analyze(tone() + noise(9))
        self.assertEqual(result["demodulation"]["bits"]["state"], "clock-required")
        self.assertEqual(result["demodulation"]["bits"]["bits"], "")
        self.assertFalse(result["demodulation"]["bits"]["verified"])

    def test_am_envelope_tracks_known_amplitude_modulation(self):
        time = np.arange(N) / RATE
        envelope = .15 * (1 + .6 * np.sin(2 * np.pi * 1_000 * time))
        data = envelope * np.exp(2j * np.pi * 125_000 * time) + noise(10)
        result = self.analyze(data)
        measured = np.asarray(result["demodulation"]["am"]["envelope"])
        self.assertGreater(np.std(measured) / np.mean(measured), .3)
        self.assertTrue(any("AM" in item["name"] for item in result["candidates"][0]["modulation"]["suggestions"]))
        self.assertFalse(result["demodulation"]["bits"]["verified"])

    def test_fm_discriminator_shows_known_frequency_swing(self):
        time = np.arange(N) / RATE
        frequency = 125_000 + 4_000 * np.sin(2 * np.pi * 800 * time)
        data = .15 * np.exp(2j * np.pi * np.cumsum(frequency) / RATE) + noise(11)
        result = self.analyze(data)
        measured = np.asarray(result["demodulation"]["fm"]["frequency_hz"])
        self.assertGreater(np.std(measured), 2_000)
        self.assertLess(np.std(measured), 4_000)

    def test_binary_fsk_candidate_bits_match_synthetic_known_clock(self):
        rng = np.random.default_rng(12)
        bits = rng.integers(0, 2, 256)
        frequency = np.repeat(100_000 + 10_000 * (2 * bits - 1), 500)
        phase = np.cumsum(2 * np.pi * frequency / RATE)
        data = .2 * np.exp(1j * phase) + noise(12, len(phase), amplitude=.0001)
        result = self.analyze(data, SignalAnalyzer(symbol_rate=2_000))
        extraction = result["demodulation"]["bits"]
        self.assertEqual(extraction["state"], "candidate-bits")
        self.assertIn("FSK", extraction["method"])
        self.assertFalse(extraction["verified"])
        # Channel edge trimming drops a few symbols. Search alignment rather
        # than assuming that an unknown stream begins on a frame boundary.
        reference = "".join(str(bit) for bit in bits)
        self.assertIn(extraction["bits"][5:-5], reference)

    def test_ook_candidate_bits_match_synthetic_known_clock(self):
        rng = np.random.default_rng(13)
        bits = rng.integers(0, 2, 256)
        amplitude = np.repeat(.015 + .18 * bits, 500)
        data = amplitude * np.exp(2j * np.pi * 125_000 * np.arange(len(amplitude)) / RATE)
        data += noise(13, len(data), amplitude=.0001)
        result = self.analyze(data, SignalAnalyzer(symbol_rate=2_000))
        extraction = result["demodulation"]["bits"]
        self.assertEqual(extraction["state"], "candidate-bits")
        self.assertIn("ASK/OOK", extraction["method"])
        self.assertFalse(extraction["verified"])
        reference = "".join(str(bit) for bit in bits)
        self.assertIn(extraction["bits"][5:-5], reference)

    def test_a_proposed_clock_does_not_turn_constant_tone_into_bits(self):
        result = self.analyze(tone() + noise(14), SignalAnalyzer(symbol_rate=2_000))
        self.assertEqual(result["demodulation"]["bits"]["state"], "insufficient-evidence")
        self.assertEqual(result["demodulation"]["bits"]["bits"], "")

    def test_invalid_input_is_rejected_without_nan_outputs(self):
        analyzer = SignalAnalyzer()
        cases = [(b"x", RATE, CENTER), (np.zeros(50, complex), RATE, CENTER),
                 (np.zeros(N), RATE, CENTER), (np.zeros((N, 2), complex), RATE, CENTER),
                 (np.full(N, complex(float("nan"), 0)), RATE, CENTER),
                 (tone(), 0, CENTER), (tone(), RATE, float("inf")),
                 (tone(), True, CENTER), (np.full(N, 1e20 + 0j), RATE, CENTER)]
        for iq, rate, center in cases:
            with self.subTest(rate=rate, center=center):
                with self.assertRaises(ValueError):
                    analyzer.process_iq(iq, rate, center, "invalid")
        for symbol_rate in [0, -1, float("nan"), True]:
            with self.assertRaises(ValueError):
                self.analyze(tone(), SignalAnalyzer(symbol_rate=symbol_rate))

    def test_snapshots_cannot_modify_internal_baseline_or_events(self):
        analyzer = SignalAnalyzer(warmup_frames=2)
        self.analyze(noise(1), analyzer)
        self.analyze(noise(2), analyzer)
        result = self.analyze(tone() + noise(3), analyzer)
        result["candidates"].clear()
        self.assertTrue(analyzer.latest()["candidates"])
        snapshot = analyzer.latest()
        snapshot["baseline"]["frames"] = 99_999
        self.assertEqual(analyzer.latest()["baseline"]["frames"], 3)
        self.assertLessEqual(len(analyzer.events()), MAX_EVENTS)


if __name__ == "__main__":
    unittest.main()
