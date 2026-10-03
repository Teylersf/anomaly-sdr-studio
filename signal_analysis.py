"""Bounded receive-only signal measurements and explicitly uncertain heuristics.

The input is complex baseband IQ or HackRF's signed-int8 interleaved bytes.
Nothing here opens a radio, transmits, identifies a signal's origin, or decrypts
encrypted traffic. Candidate bits are unframed hypotheses, never verified text.
"""

from __future__ import annotations

import base64
from collections import deque
import copy
import math

import numpy as np


MAX_SAMPLES = 262_144
MAX_EVENTS = 96
FLOOR_DB = -120.0
CEILING_DB = 0.0


def _db(power):
    return 10.0 * np.log10(np.maximum(power, 1e-15))


def _number(value, digits=5):
    return round(float(value), digits) if math.isfinite(float(value)) else 0.0


def _list(values, digits=5):
    clean = np.asarray(values, dtype=np.float64)
    return np.round(np.nan_to_num(clean, nan=0, posinf=0, neginf=0), digits).tolist()


def _reduce(values, count=512):
    """Block means preserve interval coverage without creating enormous JSON."""
    values = np.asarray(values)
    if not len(values):
        return np.array([], dtype=np.float64)
    edges = np.linspace(0, len(values), min(count, len(values)) + 1, dtype=int)
    sums = np.concatenate(([0.0], np.cumsum(values, dtype=np.float64)))
    return (sums[edges[1:]] - sums[edges[:-1]]) / np.diff(edges)


def _histogram(values, low, high, bins=64):
    high = max(float(high), float(low) + 1e-9)
    counts, edges = np.histogram(values, bins=bins, range=(low, high))
    return {"edges": _list(edges, 8), "counts": counts.tolist()}


def _levels(values):
    return np.rint(np.clip((values - FLOOR_DB) / (CEILING_DB - FLOOR_DB), 0, 1)
                   * 255).astype(np.uint8)


def _binary_clusters(values):
    """Describe two scalar clusters; also test that two clusters are justified."""
    values = np.asarray(values, dtype=np.float64)
    if len(values) < 16 or np.std(values) < 1e-10:
        return None
    centers = np.percentile(values, [20, 80])
    for _ in range(12):
        labels = values > np.mean(centers)
        if labels.all() or not labels.any():
            return None
        updated = np.array([np.mean(values[~labels]), np.mean(values[labels])])
        if np.max(np.abs(updated - centers)) < 1e-9:
            break
        centers = updated
    threshold = float(np.mean(centers))
    labels = values > threshold
    population = float(np.mean(labels))
    separation = float(centers[1] - centers[0])
    residual = values - np.where(labels, centers[1], centers[0])
    within = float(np.sqrt(np.mean(residual * residual)))
    # Uniform noise also splits into two clusters. Require well separated modes
    # and material occupancy of both states before suggesting a binary signal.
    supported = 0.12 <= population <= 0.88 and separation > max(5.0 * within, 1e-8)
    return {"centers": centers, "threshold": threshold, "separation": separation,
            "within": within, "population": population, "supported": supported}


class SignalAnalyzer:
    """Analyze bounded IQ windows; a baseline belongs to one tuned band/rate.

    Anomaly score compares each FFT bin with an exponentially weighted rolling
    mean and variance. A minimum 3 dB deviation prevents an unrealistically
    stable baseline from assigning enormous scores to tiny changes. Eight
    warmup windows are excluded from anomaly claims; alpha=0.04 then adapts
    slowly. The score is a display scale, not a probability or origin verdict.
    """

    def __init__(self, fft_size=8192, warmup_frames=8, max_candidates=12,
                 symbol_rate=None):
        if not isinstance(fft_size, int) or fft_size < 256 or fft_size > 8192 \
                or fft_size & (fft_size - 1):
            raise ValueError("fft_size must be a power of two in 256..8192")
        if not isinstance(warmup_frames, int) or not 2 <= warmup_frames <= 128:
            raise ValueError("warmup_frames must be in 2..128")
        if not isinstance(max_candidates, int) or not 1 <= max_candidates <= 32:
            raise ValueError("max_candidates must be in 1..32")
        self.fft_size = fft_size
        self.warmup_frames = warmup_frames
        self.max_candidates = max_candidates
        self.symbol_rate = symbol_rate
        self.window = np.hanning(fft_size).astype(np.float32)
        self.window_sum = float(np.sum(self.window))
        self.reset()

    def reset(self):
        self.sequence = 0
        self._frames = 0
        self._mean = None
        self._variance = None
        self._tuning = None
        self._tracks = {}
        self._next_track = 1
        self._events = deque(maxlen=MAX_EVENTS)
        self._latest = None
        self._previous_candidates = []
        self._above_threshold = set()

    def latest(self):
        return copy.deepcopy(self._latest)

    def events(self):
        return copy.deepcopy(list(self._events))

    def _input(self, iq):
        if isinstance(iq, (bytes, bytearray, memoryview)):
            if len(iq) % 2:
                raise ValueError("IQ bytes must contain complete signed-int8 I/Q pairs")
            raw = np.frombuffer(iq, dtype=np.int8).reshape(-1, 2)[-MAX_SAMPLES:]
            truncated = len(iq) // 2 > MAX_SAMPLES
            data = (raw[:, 0].astype(np.float32)
                    + 1j * raw[:, 1].astype(np.float32)) / 128.0
            clipping = np.mean((np.abs(raw[:, 0].astype(np.int16)) >= 127)
                               | (np.abs(raw[:, 1].astype(np.int16)) >= 127)) \
                if len(raw) else 0.0
        else:
            raw = np.asarray(iq)
            if raw.ndim != 1 or not np.iscomplexobj(raw):
                raise ValueError("IQ must be a one-dimensional complex array or int8 bytes")
            data = np.asarray(raw[-MAX_SAMPLES:], dtype=np.complex64)
            truncated = len(raw) > MAX_SAMPLES
            clipping = np.mean((np.abs(data.real) >= 127 / 128)
                               | (np.abs(data.imag) >= 127 / 128)) \
                if len(data) else 0.0
        if len(data) < self.fft_size:
            raise ValueError(f"at least {self.fft_size} complex samples are required")
        if not np.isfinite(data).all():
            raise ValueError("IQ contains non-finite values")
        # Very large finite arrays can overflow squared magnitudes. Reject them
        # explicitly instead of returning plausible-looking zero measurements.
        if np.max(np.abs(data)) > 1e6:
            raise ValueError("IQ amplitude exceeds the supported measurement range")
        return data, clipping, truncated

    def _fft(self, data):
        frame_count = min(32, len(data) // self.fft_size)
        blocks = data[-frame_count * self.fft_size:].reshape(frame_count, self.fft_size)
        centered_blocks = blocks - np.mean(blocks, axis=1, keepdims=True)
        transform = np.fft.fftshift(np.fft.fft(centered_blocks * self.window, axis=1), axes=1)
        power = np.abs(transform / self.window_sum) ** 2
        return _db(np.mean(power, axis=0)), _db(power)

    def _baseline(self, measured_db):
        cold = self._frames < self.warmup_frames
        if self._mean is None:
            excess = np.zeros(self.fft_size)
            z = np.zeros(self.fft_size)
        else:
            excess = measured_db - self._mean
            z = np.maximum(0, excess) / np.sqrt(np.maximum(self._variance, 9.0))
        if cold:
            z[:] = 0
        valid = np.ones(self.fft_size, dtype=bool)
        valid[self.fft_size // 2 - 3:self.fft_size // 2 + 4] = False
        global_z = float(np.max(z[valid]))
        score = min(100.0, global_z * 12.5)
        if self._mean is None:
            self._mean = measured_db.copy()
            self._variance = np.full(self.fft_size, 9.0)
        else:
            alpha = 1.0 / (self._frames + 1) if cold else 0.04
            delta = measured_db - self._mean
            self._mean += alpha * delta
            self._variance = (1 - alpha) * (self._variance + alpha * delta * delta)
        self._frames += 1
        return z, {"state": "warming" if cold else "ready", "cold_start": cold,
                   "frames": self._frames, "warmup_frames": self.warmup_frames,
                   "anomaly_score": _number(score, 1), "maximum_bin_z": _number(global_z, 3), "alpha": 0.04,
                   "minimum_sigma_db": 3,
                   "method": "Positive FFT-bin deviation from rolling mean / rolling sigma; "
                             "minimum sigma 3 dB, display score = z × 12.5, capped at 100. "
                             "Statistical novelty is not proof of an unusual source."}

    def _candidates(self, measured_db, z, sample_rate, center_hz, noise_floor,
                    detection_margin=9.0):
        bin_hz = sample_rate / self.fft_size
        valid_power = measured_db.copy()
        valid_power[self.fft_size // 2 - 3:self.fft_size // 2 + 4] = FLOOR_DB
        threshold = max(noise_floor + detection_margin, float(np.max(valid_power)) - 50)
        active = measured_db > threshold
        # The tuned center often contains a receiver DC artifact. It must not be
        # called an RF detection simply because its FFT bin is bright.
        middle = self.fft_size // 2
        active[middle - 3:middle + 4] = False
        active[0:2] = False
        active[-2:] = False
        indices = np.flatnonzero(active)
        groups = np.split(indices, np.flatnonzero(np.diff(indices) > 3) + 1) \
            if len(indices) else []
        found = []
        for group in groups:
            if not len(group) or group[0] <= middle <= group[-1]:
                continue
            start, end = int(group[0]), int(group[-1]) + 1
            band = measured_db[start:end]
            peak_position = start + int(np.argmax(band))
            peak_db = float(measured_db[peak_position])
            if peak_db - noise_floor < 9.0:
                continue
            powers = 10 ** (band / 10)
            positions = np.arange(start, end, dtype=np.float64)
            centroid_bin = float(np.sum(positions * powers) / np.sum(powers))
            offset = (centroid_bin - middle) * bin_hz
            cumulative = np.cumsum(powers) / np.sum(powers)
            lo = start + int(np.searchsorted(cumulative, .005))
            hi = start + int(np.searchsorted(cumulative, .995))
            occupied = max(bin_hz, (hi - lo + 1) * bin_hz)
            anomaly = min(100.0, float(np.percentile(z[start:end], 90)) * 12.5)
            found.append({"center_hz": _number(center_hz + offset, 3),
                          "offset_hz": _number(offset, 3),
                          "bandwidth_hz": _number(occupied, 3),
                          "snr_db": _number(peak_db - noise_floor, 2),
                          "peak_db": _number(peak_db, 2),
                          "anomaly_score": _number(anomaly, 1),
                          "anomaly_evidence": {"band_z_90": _number(np.percentile(z[start:end], 90), 3),
                                               "score_scale": 12.5, "minimum_sigma_db": 3,
                                               "label": "Positive departure from rolling spectral baseline"},
                          "bin_start": start, "bin_end": end,
                          "tags": ["narrowband" if occupied < sample_rate * .005
                                   else "wideband"],
                          "modulation": {"suggestions": [], "evidence": {}},
                          "decode": {"state": "not-selected", "verified": False}})
        found.sort(key=lambda item: item["peak_db"], reverse=True)
        return found[:self.max_candidates]

    def _track(self, candidates, bin_hz, timestamp):
        used = set()
        for item in candidates:
            tolerance = max(item["bandwidth_hz"] / 2, 4 * bin_hz)
            matches = [(abs(track["center_hz"] - item["center_hz"]), key, track)
                       for key, track in self._tracks.items()
                       if key not in used and self.sequence - track["last_seen"] <= 6
                       and abs(track["center_hz"] - item["center_hz"]) <= tolerance]
            if matches:
                _, key, track = min(matches)
                gap = self.sequence - track["last_seen"]
                track["consecutive"] = track["consecutive"] + 1 if gap == 1 else 1
                if gap > 1:
                    item["tags"].append("burst-reappearance")
            else:
                key = f"signal-{self._next_track:04d}"
                self._next_track += 1
                track = {"consecutive": 1, "last_seen": self.sequence}
                self._tracks[key] = track
                item["tags"].append("new")
            used.add(key)
            track.update(center_hz=item["center_hz"], last_seen=self.sequence,
                         bandwidth_hz=item["bandwidth_hz"], peak_db=item["peak_db"])
            item["id"] = key
            item["persistence_frames"] = track["consecutive"]
            if track["consecutive"] >= 3:
                item["tags"].append("persistent")
        # A single disappearing feature followed by one similar feature at a
        # different frequency is only a possible hop; association is uncertain.
        if len(candidates) == len(self._previous_candidates) == 1:
            old, current = self._previous_candidates[0], candidates[0]
            distance = abs(old["center_hz"] - current["center_hz"])
            ratio = current["bandwidth_hz"] / max(old["bandwidth_hz"], bin_hz)
            if current["id"] != old["id"] and .3 <= ratio <= 3 \
                    and abs(current["peak_db"] - old["peak_db"]) < 10 \
                    and distance > 4 * bin_hz:
                current["tags"].append("possible-frequency-hop")
                current["hop_evidence"] = {"previous_center_hz": old["center_hz"],
                                           "delta_hz": _number(distance, 3),
                                           "association_verified": False}
        active_alerts = {item["id"] for item in candidates if item["anomaly_score"] >= 60}
        for item in candidates:
            if item["id"] in active_alerts - self._above_threshold:
                self._events.append({"sequence": self.sequence, "timestamp": timestamp,
                                     "type": "spectral-novelty", "candidate_id": item["id"],
                                     "center_hz": item["center_hz"],
                                     "score": item["anomaly_score"],
                                     "snr_db": item["snr_db"],
                                     "bandwidth_hz": item["bandwidth_hz"],
                                     "tags": list(item["tags"]),
                                     "evidence": dict(item["anomaly_evidence"]),
                                     "label": "Statistical spectral change; origin unknown"})
        self._above_threshold = active_alerts
        self._previous_candidates = [dict(item) for item in candidates]
        self._tracks = {key: value for key, value in self._tracks.items()
                        if self.sequence - value["last_seen"] <= 60}
        if len(self._tracks) > 128:
            self._tracks = dict(sorted(self._tracks.items(),
                                      key=lambda pair: pair[1]["last_seen"],
                                      reverse=True)[:128])

    def _rhythm(self, amplitudes, sample_rate):
        reduced = _reduce(amplitudes, 2048)
        effective_rate = sample_rate * len(reduced) / len(amplitudes)
        centered = reduced - np.mean(reduced)
        energy = float(np.sum(centered * centered))
        if energy <= 1e-15:
            values = np.zeros(min(256, len(reduced)))
        else:
            transformed = np.fft.rfft(centered, n=2 * len(centered))
            values = np.fft.irfft(np.abs(transformed) ** 2)[:min(256, len(reduced))] / energy
        # Ignore adjacent lags dominated by envelope smoothing. A peak alone is
        # not symbol-clock recovery or proof that a signal contains a message.
        first = max(3, int(len(values) * .015))
        peak = first + int(np.argmax(values[first:])) if len(values) > first else 0
        return {"lags_seconds": _list(np.arange(len(values)) / effective_rate, 9),
                "correlation": _list(values),
                "peak_lag_seconds": _number(peak / effective_rate, 9),
                "peak_strength": _number(values[peak]),
                "label": "Amplitude autocorrelation; repeated structure can also come from noise or equipment"}

    def _visuals(self, data, frame_db, sample_rate, center_hz):
        amplitudes = np.abs(data)
        positions = np.linspace(0, len(data) - 1, min(1024, len(data)), dtype=int)
        phase = np.angle(data[1:] * np.conj(data[:-1]))
        frequencies = phase * sample_rate / (2 * np.pi)
        sampled_phase = phase[np.linspace(0, len(phase) - 1, min(512, len(phase)), dtype=int)]
        grouped = frame_db.reshape(len(frame_db), 256, self.fft_size // 256)
        # Average linear power, rather than averaging decibels for display bins.
        compact_db = _db(np.mean(10 ** (grouped / 10), axis=2))
        visible = frame_db.copy()
        visible[:, self.fft_size // 2 - 3:self.fft_size // 2 + 4] = FLOOR_DB
        ridge = np.argmax(visible, axis=1)
        return {"constellation": {"points": _list(np.column_stack((data.real[positions], data.imag[positions]))),
                                  "label": "Raw baseband IQ; no carrier or symbol synchronization"},
                "phase_increments": {"values": _list(sampled_phase),
                                     "instantaneous_hz": _list(_reduce(frequencies)),
                                     "histogram": _histogram(phase, -np.pi, np.pi)},
                "amplitude_histogram": _histogram(amplitudes, 0, max(float(np.max(amplitudes)), 1e-6)),
                "envelope": {"values": _list(_reduce(amplitudes)),
                             "duration_seconds": _number(len(data) / sample_rate, 9)},
                "rhythm": self._rhythm(amplitudes, sample_rate),
                "spectrogram": {"width": 256, "height": len(frame_db),
                                "power_u8": base64.b64encode(_levels(compact_db).tobytes()).decode("ascii"),
                                "floor_db": FLOOR_DB, "ceiling_db": CEILING_DB,
                                "min_hz": center_hz - sample_rate / 2,
                                "max_hz": center_hz + sample_rate / 2,
                                "ridge_hz": _list(center_hz + (ridge - self.fft_size // 2)
                                                  * sample_rate / self.fft_size, 3)}}

    def _channel(self, data, candidate, candidates, sample_rate):
        center = candidate["offset_hz"]
        width = max(candidate["bandwidth_hz"] * 2.5,
                    64 * sample_rate / self.fft_size)
        # A nearby pair of similarly strong narrow peaks could be the two tones
        # of FSK. Include both for a phase-discriminator hypothesis, without
        # merging the independent RF detections or claiming a protocol.
        neighbors = [item for item in candidates[1:]
                     if 4 * sample_rate / self.fft_size < abs(item["offset_hz"] - center)
                     < sample_rate * .025 and abs(item["peak_db"] - candidate["peak_db"]) < 12]
        paired = False
        if neighbors:
            other = min(neighbors, key=lambda item: abs(item["offset_hz"] - center))
            distance = abs(other["offset_hz"] - center)
            center = (center + other["offset_hz"]) / 2
            width = max(width, distance * 1.8 + other["bandwidth_hz"] * 2)
            paired = True
        width = min(width, sample_rate * .8)
        shifted = data * np.exp(-2j * np.pi * center * np.arange(len(data)) / sample_rate)
        transformed = np.fft.fft(shifted)
        bins = np.fft.fftfreq(len(data), 1 / sample_rate)
        transformed[np.abs(bins) > width / 2] = 0
        filtered = np.fft.ifft(transformed)
        stride = max(1, int(sample_rate / (width * 2.5)))
        channel = filtered[::stride]
        trim = min(len(channel) // 20, 128)
        if trim:
            channel = channel[trim:-trim]
        return channel, sample_rate / stride, center, width, paired

    def _bits(self, amplitudes, frequencies, channel_rate, amplitude_model, frequency_model):
        rate = self.symbol_rate
        empty = {"state": "clock-required" if rate is None else "insufficient-evidence",
                 "symbol_rate": rate, "bits": "", "count": 0, "verified": False,
                 "method": "Exploratory binary slicing; no framing, CRC, text decoding, or decryption"}
        if rate is None:
            empty["reason"] = "Supply a known or independently estimated symbol rate to inspect candidate bits"
            return empty
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) \
                or not math.isfinite(rate) or rate <= 0:
            raise ValueError("symbol_rate must be positive and finite, or None")
        samples_per_symbol = channel_rate / rate
        if samples_per_symbol < 8:
            empty["reason"] = "At least eight channel samples per proposed symbol are required"
            return empty
        methods = []
        if frequency_model and frequency_model["supported"]:
            methods.append(("binary-FSK hypothesis", frequencies, frequency_model))
        if amplitude_model and amplitude_model["supported"]:
            methods.append(("ASK/OOK hypothesis", amplitudes, amplitude_model))
        best = None
        for method, values, model in methods:
            cumulative = np.concatenate(([0.0], np.cumsum(values, dtype=np.float64)))
            for offset in np.linspace(0, samples_per_symbol, min(64, math.ceil(samples_per_symbol)), endpoint=False):
                edges = np.rint(np.arange(offset, len(values), samples_per_symbol)).astype(int)
                if len(edges) < 17:
                    continue
                symbols = (cumulative[edges[1:]] - cumulative[edges[:-1]]) / np.maximum(np.diff(edges), 1)
                clusters = _binary_clusters(symbols)
                if not clusters or not clusters["supported"]:
                    continue
                quality = clusters["separation"] / max(clusters["within"], 1e-8)
                bits = symbols > clusters["threshold"]
                if best is None or quality > best[0]:
                    best = (quality, method, bits, offset, clusters)
        if best is None:
            empty["reason"] = "No supported two-state signal at this proposed clock; noise is not decoded as a message"
            return empty
        quality, method, bits, offset, clusters = best
        bit_string = "".join("1" if bit else "0" for bit in bits[:2048])
        return {"state": "candidate-bits", "symbol_rate": _number(rate),
                "bits": bit_string, "count": len(bit_string), "verified": False,
                "method": method, "symbol_phase_samples": _number(offset, 3),
                "cluster_separation_ratio": _number(min(quality, 1000), 2),
                "polarity": "Hypothesis only; bit polarity and framing are unknown",
                "reason": "Two states fit the proposed clock; this does not validate a message"}

    def _demodulate(self, data, candidates, sample_rate):
        if not candidates:
            return {"candidate_id": None, "state": "no-candidate", "am": {"envelope": []},
                    "fm": {"frequency_hz": []},
                    "bits": {"state": "no-candidate", "bits": "", "count": 0, "verified": False}}
        primary = candidates[0]
        channel, channel_rate, mixed_center, width, paired = self._channel(data, primary, candidates, sample_rate)
        amplitudes = np.abs(channel)
        phase = np.angle(channel[1:] * np.conj(channel[:-1]))
        frequencies = phase * channel_rate / (2 * np.pi)
        amplitude_cv = float(np.std(amplitudes) / max(float(np.mean(amplitudes)), 1e-12))
        valid_phase = (amplitudes[1:] > np.percentile(amplitudes, 15)) \
            & (amplitudes[:-1] > np.percentile(amplitudes, 15))
        phase_values = frequencies[valid_phase]
        frequency_model = _binary_clusters(phase_values)
        amplitude_model = _binary_clusters(amplitudes)
        phase_std = float(np.std(phase_values)) if len(phase_values) else 0.0
        suggestions = []
        if amplitude_cv < .1 and phase_std < max(200, width * .015):
            suggestions.append({"name": "Carrier / tone-like", "confidence": "medium",
                                "reason": "Nearly constant filtered amplitude and phase increment"})
        if amplitude_cv > .12:
            suggestions.append({"name": "AM / ASK / changing envelope", "confidence": "low",
                                "reason": "Filtered amplitude varies; fading and overlapping signals can do this too"})
        if amplitude_model and amplitude_model["supported"]:
            suggestions.append({"name": "ASK / OOK candidate", "confidence": "low",
                                "reason": "Two separated amplitude populations; symbol clock and framing remain unverified"})
        if frequency_model and frequency_model["supported"]:
            suggestions.append({"name": "Binary FSK candidate", "confidence": "low",
                                "reason": "Two separated instantaneous-frequency populations; could also be overlapping tones"})
        elif phase_std > max(500, width * .03):
            suggestions.append({"name": "FM / phase-varying candidate", "confidence": "low",
                                "reason": "Phase increments vary; this alone does not identify a modulation or protocol"})
        if not suggestions:
            suggestions.append({"name": "Unknown", "confidence": "low",
                                "reason": "No strong amplitude or phase heuristic; more samples and protocol evidence are needed"})
        bits = self._bits(amplitudes, frequencies, channel_rate, amplitude_model, frequency_model)
        primary["modulation"] = {"suggestions": suggestions,
                                 "evidence": {"amplitude_cv": _number(amplitude_cv),
                                              "phase_std_hz": _number(phase_std, 2),
                                              "nearby_tone_pair": paired,
                                              "channel_bandwidth_hz": _number(width),
                                              "channel_sample_rate": _number(channel_rate)}}
        primary["decode"] = bits
        return {"candidate_id": primary["id"], "state": "exploratory-demodulation",
                "mixed_offset_hz": _number(mixed_center),
                "channel_sample_rate": _number(channel_rate),
                "am": {"envelope": _list(_reduce(amplitudes)),
                       "label": "Filtered magnitude / AM envelope; relative amplitude, no protocol identification"},
                "fm": {"frequency_hz": _list(_reduce(frequencies)),
                       "label": "Filtered phase discriminator relative to mixed center; exploratory FM trace"},
                "bits": bits}

    def process_iq(self, iq, sample_rate, center_hz, timestamp):
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, float)) \
                or not math.isfinite(sample_rate) or sample_rate <= 0:
            raise ValueError("sample_rate must be positive and finite")
        if isinstance(center_hz, bool) or not isinstance(center_hz, (int, float)) \
                or not math.isfinite(center_hz):
            raise ValueError("center_hz must be finite")
        if self.symbol_rate is not None and (isinstance(self.symbol_rate, bool)
                or not isinstance(self.symbol_rate, (int, float))
                or not math.isfinite(self.symbol_rate) or self.symbol_rate <= 0):
            raise ValueError("symbol_rate must be positive and finite, or None")
        data, clipping, truncated = self._input(iq)
        tuning = (float(sample_rate), float(center_hz))
        if self._tuning is not None and self._tuning != tuning:
            self.reset()
        self._tuning = tuning
        self.sequence += 1
        measured_db, frames_db = self._fft(data)
        clean = np.concatenate((measured_db[:self.fft_size // 2 - 3],
                                measured_db[self.fft_size // 2 + 4:]))
        noise_floor = float(np.percentile(clean, 25))
        # One FFT has exponentially distributed noise-bin power. Use a higher
        # threshold until eight independent FFTs have reduced that variance.
        detection_margin = 9.0 + 12.0 * max(0.0, (8 - len(frames_db)) / 7)
        usable_energy = bool(np.max(clean) > -149.9)
        if usable_energy:
            z, baseline = self._baseline(measured_db)
            baseline["measurement_available"] = True
        else:
            z = np.zeros(self.fft_size)
            baseline = {"state": "warming" if self._frames < self.warmup_frames else "ready",
                        "frames": self._frames, "warmup_frames": self.warmup_frames,
                        "cold_start": self._frames < self.warmup_frames,
                        "anomaly_score": 0, "measurement_available": False,
                        "method": "No usable non-DC energy; baseline is unchanged"}
        candidates = self._candidates(measured_db, z, sample_rate, center_hz,
                                      noise_floor, detection_margin)
        stamp = str(timestamp)[:80]
        self._track(candidates, sample_rate / self.fft_size, stamp)
        visuals = self._visuals(data, frames_db, sample_rate, center_hz)
        demodulation = self._demodulate(data, candidates, sample_rate)
        amplitudes = np.abs(data)
        result = {"sequence": self.sequence, "timestamp": stamp,
                  "sample_rate": sample_rate, "center_hz": center_hz,
                  "samples_analyzed": len(data), "input_truncated": truncated,
                  "metrics": {"power_dbfs": _number(_db(np.mean(amplitudes ** 2)), 2),
                              "peak_amplitude": _number(np.max(amplitudes)),
                              "clipped_percent": _number(clipping * 100, 4)},
                  "spectrum": {"fft_size": self.fft_size,
                               "bin_width_hz": sample_rate / self.fft_size,
                               "min_hz": center_hz - sample_rate / 2,
                               "max_hz": center_hz + sample_rate / 2,
                               "power_db": _list(measured_db, 2),
                               "noise_floor_db": _number(noise_floor, 2),
                               "noise_floor_available": noise_floor > -149.9,
                               "waterfall_u8": base64.b64encode(_levels(measured_db).tobytes()).decode("ascii"),
                               "floor_db": FLOOR_DB, "ceiling_db": CEILING_DB,
                               "dc_mask_bins": 7,
                               "dc_removed": True,
                               "detection_threshold_db": _number(max(noise_floor + detection_margin,
                                                                      max(clean) - 50), 2),
                               "detection_margin_db": _number(detection_margin, 2),
                               "averaged_fft_frames": len(frames_db),
                               "power_reference": "Relative dBFS; uncalibrated receiver"},
                  "baseline": baseline, "candidates": candidates,
                  "visuals": visuals, "demodulation": demodulation,
                  "events": self.events(),
                  "interpretation": "Measured RF features and uncertain hypotheses. Novelty does not identify an origin, "
                                    "and candidate bits are neither verified messages nor decrypted data."}
        self._latest = result
        return copy.deepcopy(result)
