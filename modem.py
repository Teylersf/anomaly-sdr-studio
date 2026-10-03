"""Small continuous-phase 2-FSK modem for signed 8-bit interleaved I/Q.

This module generates and inspects byte buffers only; it never accesses a radio.
Bits are MSB first. A frame is 64 alternating preamble bits, a 32-bit sync word,
a big-endian uint16 length, the payload, and a big-endian CRC32 of the payload.
The I/Q tones are offset_hz +/- deviation_hz, with the upper tone representing 1.
"""

from __future__ import annotations

import math
import struct
import zlib

import numpy as np


SYNC = b"\xd3\x91\xda\x26"
MAX_PAYLOAD_BYTES = 4096
PREAMBLE_BITS = 64


def _parameters(sample_rate: int, baud: int, deviation_hz: float,
                offset_hz: float) -> tuple[int, float, float]:
    if (not isinstance(sample_rate, (int, np.integer))
            or not isinstance(baud, (int, np.integer))
            or sample_rate <= 0 or baud <= 0):
        raise ValueError("sample_rate and baud must be positive integers")
    if sample_rate % baud:
        raise ValueError("sample_rate must be an integer multiple of baud")
    samples_per_bit = sample_rate // baud
    if samples_per_bit < 8:
        raise ValueError("at least 8 samples per bit are required")
    deviation_hz, offset_hz = float(deviation_hz), float(offset_hz)
    if not math.isfinite(deviation_hz) or deviation_hz <= 0:
        raise ValueError("deviation_hz must be positive and finite")
    if not math.isfinite(offset_hz):
        raise ValueError("offset_hz must be finite")
    if abs(offset_hz) + deviation_hz >= sample_rate / 2:
        raise ValueError("both FSK tones must be inside the sample-rate Nyquist band")
    return samples_per_bit, deviation_hz, offset_hz


def encode_iq(payload: bytes, sample_rate: int = 8_000_000,
              baud: int = 8_000, deviation_hz: float = 20_000,
              offset_hz: float = 100_000, amplitude: float = 8) -> bytes:
    """Return one framed 2-FSK burst as interleaved signed-int8 I,Q bytes.

    amplitude is the peak value of either I or Q, constrained to 1..16.
    Defaults produce (144 + 8 * len(payload)) / 8000 seconds of I/Q.
    """
    samples_per_bit, deviation_hz, offset_hz = _parameters(
        sample_rate, baud, deviation_hz, offset_hz)
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError("payload must be bytes-like")
    payload = bytes(payload)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
    amplitude = float(amplitude)
    if not math.isfinite(amplitude) or not 1 <= amplitude <= 16:
        raise ValueError("amplitude must be finite and in the range 1..16")

    frame = (SYNC + struct.pack(">H", len(payload)) + payload
             + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF))
    bits = np.concatenate((np.tile(np.array([1, 0], dtype=np.uint8), 32),
                           np.unpackbits(np.frombuffer(frame, dtype=np.uint8))))
    output = bytearray()
    phase = 0.0
    # Bounded temporary arrays also keep a maximum-size payload inexpensive.
    for start in range(0, len(bits), 256):
        frequencies = offset_hz + deviation_hz * (
            2.0 * bits[start:start + 256].astype(np.float64) - 1.0)
        increments = np.repeat(2 * np.pi * frequencies / sample_rate,
                               samples_per_bit)
        angles = phase + np.cumsum(increments)
        iq = np.empty((len(angles), 2), dtype=np.int8)
        iq[:, 0] = np.rint(amplitude * np.cos(angles)).astype(np.int8)
        iq[:, 1] = np.rint(amplitude * np.sin(angles)).astype(np.int8)
        output.extend(iq.tobytes())
        phase = float(angles[-1] % (2 * np.pi))
    return bytes(output)


def decode_iq(iq: bytes, sample_rate: int = 8_000_000,
              baud: int = 8_000, deviation_hz: float = 20_000,
              offset_hz: float = 100_000) -> list[bytes]:
    """Return complete frames with matching sync, bounded length and CRC32.

    Accepts arbitrary leading sample alignment and multiple bursts. The caller
    must retain a whole frame across capture chunks. Carrier error should be
    materially smaller than deviation_hz; this is a fixed-rate modem without
    clock recovery, forward error correction, or arbitrary-frequency search.
    A stream downsampled 25:1 from 8 MS/s can be decoded at sample_rate=320000.
    """
    samples_per_bit, deviation_hz, offset_hz = _parameters(
        sample_rate, baud, deviation_hz, offset_hz)
    if len(iq) % 2:
        raise ValueError("I/Q data must contain complete two-byte sample pairs")
    if len(iq) < 2 * samples_per_bit * (32 + 16 + 32):
        return []

    # Keep >= 8 discriminator samples per symbol. Known carrier phase is
    # removed from each product before atan2, including after stride aliasing.
    maximum_stride = min(64, samples_per_bit // 8,
                         max(1, int(sample_rate / (8 * deviation_hz))))
    stride = max(value for value in range(1, maximum_stride + 1)
                 if samples_per_bit % value == 0)
    symbol_samples = samples_per_bit // stride
    raw = np.frombuffer(iq, dtype=np.int8).reshape(-1, 2)[::stride]
    i = raw[:, 0].astype(np.float32)
    q = raw[:, 1].astype(np.float32)
    real = i[1:] * i[:-1] + q[1:] * q[:-1]
    imag = q[1:] * i[:-1] - i[1:] * q[:-1]
    carrier_phase = 2 * np.pi * offset_hz * stride / sample_rate
    cosine, sine = math.cos(carrier_phase), math.sin(carrier_phase)
    discriminator = np.arctan2(imag * cosine - real * sine,
                                real * cosine + imag * sine)
    integrated = np.empty(len(discriminator) + 1, dtype=np.float64)
    integrated[0] = 0
    np.cumsum(discriminator, dtype=np.float64, out=integrated[1:])
    candidates: list[tuple[int, bytes]] = []

    # Every symbol phase is tested on the reduced-rate stream, using O(N)
    # prefix sums, rather than rescanning the original high-rate samples.
    for timing in range(symbol_samples):
        starts = np.arange(timing, len(discriminator) - symbol_samples + 1,
                           symbol_samples)
        if len(starts) < 80:
            continue
        bits = ((integrated[starts + symbol_samples]
                 - integrated[starts]) > 0).astype(np.uint8)
        for bit_alignment in range(8):
            full_bytes = (len(bits) - bit_alignment) // 8
            if full_bytes < 10:
                continue
            packed = np.packbits(bits[bit_alignment:]).tobytes()[:full_bytes]
            search_from = 0
            while True:
                position = packed.find(SYNC, search_from)
                if position < 0:
                    break
                search_from = position + 1
                if position + 6 > len(packed):
                    continue
                length = int.from_bytes(packed[position + 4:position + 6], "big")
                end = position + 10 + length
                if length > MAX_PAYLOAD_BYTES or end > len(packed):
                    continue
                payload = packed[position + 6:position + 6 + length]
                checksum = int.from_bytes(packed[end - 4:end], "big")
                if (zlib.crc32(payload) & 0xFFFFFFFF) != checksum:
                    continue
                sample_position = (timing + (bit_alignment + position * 8)
                                   * symbol_samples) * stride
                candidates.append((sample_position, payload))

    # Many timing phases decode the same burst. Collapse these while retaining
    # separately transmitted frames even when their payloads are identical.
    decoded: list[bytes] = []
    previous_position = -3 * samples_per_bit
    previous_payload: bytes | None = None
    for position, payload in sorted(candidates, key=lambda item: item[0]):
        if (payload == previous_payload
                and position - previous_position <= 2 * samples_per_bit):
            continue
        decoded.append(payload)
        previous_position, previous_payload = position, payload
    return decoded
