"""Offline modem verification. These tests never call hardware tools."""

import unittest
from unittest.mock import patch

import numpy as np

from modem import decode_iq, encode_iq


def channel(iq, *, sample_rate, carrier_error=0, gain=1, noise=0, seed=12):
    raw = np.frombuffer(iq, dtype=np.int8).reshape(-1, 2).astype(np.float64)
    signal = raw[:, 0] + 1j * raw[:, 1]
    phase = 2 * np.pi * carrier_error * np.arange(len(signal)) / sample_rate
    signal = signal * gain * np.exp(1j * phase)
    rng = np.random.default_rng(seed)
    signal += noise * (rng.normal(size=len(signal))
                       + 1j * rng.normal(size=len(signal)))
    output = np.empty((len(signal), 2), dtype=np.int8)
    output[:, 0] = np.clip(np.rint(signal.real), -128, 127).astype(np.int8)
    output[:, 1] = np.clip(np.rint(signal.imag), -128, 127).astype(np.int8)
    return output.tobytes()


class ModemTests(unittest.TestCase):
    def test_clean_default_burst_and_format(self):
        payload = b"2,3,5,7,11,13,17,19,23,29"
        iq = encode_iq(payload)
        self.assertEqual(len(iq), 2 * 1000 * (144 + 8 * len(payload)))
        self.assertLessEqual(np.abs(np.frombuffer(iq, dtype=np.int8)).max(), 8)
        self.assertEqual(decode_iq(iq), [payload])

    def test_downsampled_capture_with_non_aligned_padding(self):
        payload = bytes(range(124))
        full_rate = np.frombuffer(encode_iq(payload), dtype=np.int8).reshape(-1, 2)
        # A receiver's global 25:1 sample stride has arbitrary burst alignment.
        padded = np.concatenate((np.zeros((1739, 2), dtype=np.int8), full_rate,
                                 np.zeros((40019, 2), dtype=np.int8)))
        iq = padded[::25].tobytes()
        self.assertEqual(decode_iq(iq, sample_rate=320_000), [payload])

    def test_noise_carrier_error_and_positive_amplitude_variation(self):
        payload = b"ASDR1|session=offline|seq=4|primes=2,3,5,7,11,13"
        clean = encode_iq(payload, sample_rate=320_000)
        for carrier_error, gain, noise in [(7000, 0.6, 1.4), (-6500, 1.5, 3.0)]:
            with self.subTest(carrier_error=carrier_error):
                noisy = channel(clean, sample_rate=320_000,
                                carrier_error=carrier_error, gain=gain, noise=noise)
                padded = bytes(2 * 713) + noisy + bytes(2 * 137)
                self.assertEqual(decode_iq(padded, sample_rate=320_000), [payload])

    def test_amplitude_limits_and_empty_payload(self):
        for amplitude in [1, 16]:
            with self.subTest(amplitude=amplitude):
                iq = encode_iq(b"", sample_rate=320_000, amplitude=amplitude)
                self.assertEqual(decode_iq(iq, sample_rate=320_000), [b""])
        for amplitude in [0, 17, float("nan")]:
            with self.assertRaises(ValueError):
                encode_iq(b"x", amplitude=amplitude)

    def test_multiple_frames_preserve_repeated_payloads(self):
        first = encode_iq(b"first", sample_rate=320_000)
        second = encode_iq(b"second", sample_rate=320_000)
        capture = bytes(2 * 81) + first + bytes(2 * 197) + second + first
        self.assertEqual(decode_iq(capture, sample_rate=320_000),
                         [b"first", b"second", b"first"])

    def test_crc_failure_and_truncation(self):
        with patch("modem.zlib.crc32", return_value=0x12345678):
            invalid = encode_iq(b"CRC must fail", sample_rate=320_000)
        self.assertEqual(decode_iq(invalid, sample_rate=320_000), [])
        valid = encode_iq(b"truncated", sample_rate=320_000)
        self.assertEqual(decode_iq(valid[:-2 * 40 * 16], sample_rate=320_000), [])

    def test_random_noise_does_not_decode(self):
        rng = np.random.default_rng(97)
        random_iq = rng.integers(-20, 21, size=600_000, dtype=np.int8).tobytes()
        self.assertEqual(decode_iq(random_iq, sample_rate=320_000), [])
        self.assertEqual(decode_iq(bytes(640_000), sample_rate=320_000), [])

    def test_invalid_sizes_and_parameters(self):
        with self.assertRaises(ValueError):
            encode_iq(bytes(4097))
        with self.assertRaises(ValueError):
            decode_iq(b"\x00")
        with self.assertRaises(ValueError):
            encode_iq(b"x", sample_rate=320_001)
        with self.assertRaises(ValueError):
            encode_iq(b"x", offset_hz=4_000_000)
        self.assertEqual(decode_iq(b""), [])


if __name__ == "__main__":
    unittest.main()
