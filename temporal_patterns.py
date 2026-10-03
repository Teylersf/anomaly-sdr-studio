"""Bounded patterns across observed spectrum updates; no fabricated samples.

Occupancy is a fraction of observed frames, not continuous wall-clock duty.
Recurrence compares active frequency footprints with Jaccard similarity; two
empty footprints deliberately have similarity zero. Repetition estimates use
only the latest uninterrupted, reasonably regular sequence of measurements.
"""

from __future__ import annotations

import base64
from collections import deque
from datetime import datetime, timezone
import math

import numpy as np


FFT_SIZE = 8192


def _number(value):
    value = float(value)
    return round(value, 9) if math.isfinite(value) else 0.0


def _stamp(value):
    if isinstance(value, bool):
        raise ValueError("timestamp must be a finite epoch time or ISO timestamp")
    if isinstance(value, (int, float)) and math.isfinite(value):
        moment = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("timestamp must be a finite epoch time or ISO timestamp") from error
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        moment = moment.astimezone(timezone.utc)
    else:
        raise ValueError("timestamp must be a finite epoch time or ISO timestamp")
    return moment.timestamp(), moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


class TemporalPatterns:
    def __init__(self, max_frames=128, columns=256):
        if isinstance(max_frames, bool) or not isinstance(max_frames, int) \
                or not 2 <= max_frames <= 128:
            raise ValueError("max_frames must be an integer in 2..128")
        if isinstance(columns, bool) or not isinstance(columns, int) \
                or not 1 <= columns <= 256 or FFT_SIZE % columns:
            raise ValueError("columns must divide 8192 and be in 1..256")
        self.max_frames = max_frames
        self.columns = columns
        self.reset()

    def reset(self):
        self._frames = deque(maxlen=self.max_frames)
        self._tuning = None
        self._gap_pending = True

    def gap(self):
        self._gap_pending = True

    def _latest_segment(self):
        frames = list(self._frames)
        start = 0
        for index, frame in enumerate(frames):
            if frame["gap_before"]:
                start = index
        return frames[start:]

    def _periodicity(self):
        frames = self._latest_segment()
        output = {"lag_seconds": [], "correlation": [], "peak_seconds": None,
                  "state": "insufficient",
                  "evidence": "Need at least 30 measured frames in one uninterrupted sequence; "
                              "no samples are invented across gaps"}
        n = len(frames)
        if n < 30:
            return output
        times = np.array([frame["epoch"] for frame in frames])
        intervals = np.diff(times)
        cadence = float(np.median(intervals))
        if cadence <= 0 or np.max(np.abs(intervals - cadence)) > cadence * .25:
            output["evidence"] = ("Irregular frame timing prevents a reliable repetition estimate; "
                                  "measurements were not resampled to hide missing episodes")
            return output
        values = np.array([frame["power_db"] for frame in frames])
        values -= np.mean(values)
        energy = float(np.sum(values ** 2))
        if energy <= 1e-12:
            output.update(state="no-repeat-peak", evidence="Observed relative band power is constant; "
                          "a persistent frequency footprint can still recur in the matrix")
            return output
        fft_size = 2 ** math.ceil(math.log2(2 * n))
        transformed = np.fft.rfft(values, n=fft_size)
        max_candidate_lag = (n - 1) // 2
        # One extra neighboring lag permits a local-peak test at the limit.
        lag_count = min(n, max_candidate_lag + 2)
        correlation = np.fft.irfft(np.abs(transformed) ** 2, n=fft_size)[:lag_count]
        correlation /= energy
        correlation *= n / (n - np.arange(lag_count))
        correlation = np.clip(correlation, -1, 1)
        output.update(lag_seconds=[_number(index * cadence) for index in range(lag_count)],
                      correlation=[_number(value) for value in correlation],
                      state="no-repeat-peak",
                      evidence="No sufficiently strong local repetition peak with at least two "
                               "observed cycles; this is a time-sampled heuristic, not proof of absence")
        threshold = max(.45, min(.75, 3 * math.sqrt(math.log(2 * lag_count) / n)))
        candidates = []
        for lag in range(2, max_candidate_lag + 1):
            peak = float(correlation[lag])
            if peak <= threshold or peak <= correlation[lag - 1] \
                    or peak < correlation[lag + 1]:
                continue
            radius = max(2, int(lag * .15))
            left = float(np.min(correlation[max(1, lag - radius):lag]))
            right = float(np.min(correlation[lag + 1:min(lag_count, lag + radius + 1)]))
            prominence = peak - max(left, right)
            if prominence > .1 and lag * cadence * 2 <= times[-1] - times[0] + 1e-6:
                candidates.append((lag, peak, prominence))
        if candidates:
            # Favor the earliest well-supported repeat rather than its longer
            # harmonics. Nearby peaks are not authenticated protocol clocks.
            lag, peak, prominence = candidates[0]
            output.update(state="candidate", peak_seconds=_number(lag * cadence),
                          evidence=f"Candidate repeat {lag * cadence:.6g} s; correlation {peak:.3f}, "
                                   f"local prominence {prominence:.3f}, threshold {threshold:.3f}, "
                                   f"{n} contiguous measured frames. Multiple lags are tested; "
                                   "this is not a calibrated significance or message claim")
        return output

    def append(self, power_db8192, noise_floor_db, timestamp, sample_rate, center_hz):
        measured = np.asarray(power_db8192, dtype=np.float64)
        if measured.ndim != 1 or len(measured) != FFT_SIZE or not np.isfinite(measured).all():
            raise ValueError("power_db8192 must contain 8192 finite spectral levels")
        if isinstance(noise_floor_db, bool) or not isinstance(noise_floor_db, (int, float)) \
                or not math.isfinite(noise_floor_db):
            raise ValueError("noise_floor_db must be finite")
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, float)) \
                or not math.isfinite(sample_rate) or sample_rate <= 0:
            raise ValueError("sample_rate must be positive and finite")
        if isinstance(center_hz, bool) or not isinstance(center_hz, (int, float)) \
                or not math.isfinite(center_hz):
            raise ValueError("center_hz must be finite")
        epoch, time_text = _stamp(timestamp)
        tuning = (float(sample_rate), float(center_hz))
        if self._tuning is not None and self._tuning != tuning:
            self.reset()
        self._tuning = tuning
        if self._frames:
            delta = epoch - self._frames[-1]["epoch"]
            if delta <= 0:
                self.gap()
            else:
                recent = self._latest_segment()[-8:]
                differences = [right["epoch"] - left["epoch"]
                               for left, right in zip(recent, recent[1:])]
                if len(differences) >= 3 and delta > 3 * float(np.median(differences)):
                    self.gap()
        active = measured > noise_floor_db + 9
        active[FFT_SIZE // 2 - 3:FFT_SIZE // 2 + 4] = False
        active[:2] = False
        active[-2:] = False
        mask = active.reshape(self.columns, FFT_SIZE // self.columns).any(axis=1)
        valid = np.ones(FFT_SIZE, dtype=bool)
        valid[FFT_SIZE // 2 - 3:FFT_SIZE // 2 + 4] = False
        # Log-sum-exp scaling keeps even extreme finite inputs from overflowing.
        maximum = float(np.max(measured[valid]))
        relative = 10 ** ((measured[valid] - maximum) / 10)
        power = maximum + 10 * math.log10(max(float(np.mean(relative)), 1e-300))
        self._frames.append({"epoch": epoch, "time": time_text,
                             "power_db": _number(power), "mask": mask,
                             "gap_before": self._gap_pending})
        self._gap_pending = False
        return self.snapshot()

    def snapshot(self):
        frames = list(self._frames)
        n = len(frames)
        sample_rate, center_hz = self._tuning or (0.0, 0.0)
        frequencies = center_hz - sample_rate / 2 \
                      + (np.arange(self.columns) + .5) * sample_rate / self.columns
        if n:
            masks = np.stack([frame["mask"] for frame in frames]).astype(np.uint16)
            intersection = masks @ masks.T
            populations = np.sum(masks, axis=1)
            union = populations[:, None] + populations[None, :] - intersection
            similarity = np.divide(intersection, union, out=np.zeros((n, n), float), where=union > 0)
            quantized = np.rint(similarity * 255).astype(np.uint8)
            duty = np.mean(masks, axis=0)
            span = max(0.0, frames[-1]["epoch"] - frames[0]["epoch"])
        else:
            quantized = np.zeros((0, 0), dtype=np.uint8)
            duty = np.zeros(self.columns)
            span = 0.0
        return {
            "occupancy": {"frequencies_hz": [_number(value) for value in frequencies],
                          "duty_cycle": [_number(value) for value in duty],
                          "frames": n, "span_seconds": _number(span)},
            "recurrence": {"width": n, "height": n,
                           "similarity_u8": base64.b64encode(quantized.tobytes()).decode("ascii"),
                           "gap_rows": [frame["gap_before"] for frame in frames],
                           "interpretation": "Jaccard similarity of active frequency columns "
                                             "at measured instants; empty pairs are zero. "
                                             "Matches can be ordinary persistent or repeated bands, "
                                             "including comparisons across marked gaps. "
                                             "This is not a lossless continuous recording or a message."},
            "power_timeline": {"times": [frame["time"] for frame in frames],
                               "power_db": [frame["power_db"] for frame in frames]},
            "periodicity": self._periodicity(),
        }
