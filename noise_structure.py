"""Bounded, descriptive departures from independent circular Gaussian noise.

These are exploratory waveform measurements, not a randomness certification,
calibrated statistical significance test, modulation decoder, or origin claim.
Colored Gaussian noise, filtering, clipping, and ordinary carriers can all have
strong structure. The input is normalized complex I/Q, with digital full scale
at approximately +/-1 per component for the optional clipping indicator.
"""

from __future__ import annotations

import math

import numpy as np


MAX_SAMPLES = 16_384
MAX_LAGS = 512
MAX_PERIOD_BINS = 256
RAYLEIGH_EXCESS_KURTOSIS = 0.24508930068763846


def _finite(value):
    value = float(value)
    return round(value, 9) if math.isfinite(value) else 0.0


def _strength(value):
    return float(np.clip(value, 0.0, 1.0))


def _reference(count, lag_count, envelope_bins):
    count = max(1, count)
    # These sample-count-dependent scales motivate conservative display
    # thresholds. They do NOT yield calibrated p-values for windowed, dependent,
    # selected, or repeatedly inspected samples.
    return {
        "model": "Independent, zero-mean, circular complex Gaussian samples",
        "interpretation": "Structure score is a descriptive display scale, not a probability, "
                          "a cryptographic randomness test, a message, or evidence of intelligence. "
                          "Colored or filtered Gaussian noise can have strong structure.",
        "multiple_comparisons": "Many features and lags are examined repeatedly; thresholds are "
                                "heuristic and do not provide a calibrated false-alarm rate.",
        "clipping_assumption": "I/Q components are normalized to digital full scale near +/-1; "
                               "near-full-scale counts alone do not prove clipping.",
        "window_rule": f"Most recent contiguous {MAX_SAMPLES} samples at most; no stride decimation",
        "thresholds": {
            "autocorrelation": _finite(4 * math.sqrt(math.log(max(2, 2 * lag_count)) / count)),
            "phase_concentration": _finite(4 / math.sqrt(count)),
            "i_q_correlation": _finite(4 / math.sqrt(count)),
            "circularity_coefficient": _finite(4 * math.sqrt(2 / count)),
            "sign_runs_abs_z": 6,
            "periodicity_peak": _finite(min(.25, 8 * math.log(max(2, envelope_bins))
                                               / max(1, envelope_bins))),
            "spectral_flatness": .65,
            "normalized_spectral_entropy": .96,
            "amplitude_kurtosis_distance": .75,
        },
    }


def _empty(count, sample_rate, state):
    return {
        "structure_score": 0.0,
        "state": state,
        "samples_analyzed": count,
        "window_duration_seconds": _finite(count / sample_rate),
        "metrics": {
            "spectral_flatness": 0.0,
            "normalized_spectral_entropy": 0.0,
            "complex_autocorrelation_peak": 0.0,
            "complex_autocorrelation_lag_seconds": 0.0,
            "periodicity_peak": 0.0,
            "periodicity_hz": 0.0,
            "amplitude_excess_kurtosis": 0.0,
            "amplitude_kurtosis_available": False,
            "phase_concentration": 0.0,
            "i_q_correlation": 0.0,
            "circularity_coefficient": 0.0,
            "sign_runs_z": 0.0,
            "clipped_percent": 0.0,
        },
        "flags": [],
        "autocorrelation": {"lags_seconds": [], "values": []},
        "periodicity_spectrum": {"frequency_hz": [], "power_relative": []},
        "reference": _reference(count, min(MAX_LAGS, count), max(1, count // 2)),
    }


def _flag(flags, label, evidence, strength):
    strength = _strength(strength)
    if strength >= .08:
        flags.append({"label": label, "evidence": evidence,
                      "strength": _finite(strength)})


def _runs_z(values):
    signs = np.asarray(values >= 0)
    positive = int(np.count_nonzero(signs))
    negative = len(signs) - positive
    if min(positive, negative) == 0 or len(signs) < 3:
        return 0.0
    runs = 1 + int(np.count_nonzero(signs[1:] != signs[:-1]))
    product = positive * negative
    n = len(signs)
    expected = 1 + 2 * product / n
    variance = 2 * product * (2 * product - n) / (n * n * (n - 1))
    return (runs - expected) / math.sqrt(variance) if variance > 0 else 0.0


def _spectral_metrics(centered):
    # Four averaged periodograms reduce the large bin-to-bin variation of one
    # Gaussian-noise periodogram. Flatness still need not be exactly one.
    block_count = min(4, len(centered) // 64)
    block_size = 2 ** int(math.log2(len(centered) // block_count))
    blocks = centered[-block_count * block_size:].reshape(block_count, block_size)
    blocks = blocks - np.mean(blocks, axis=1, keepdims=True)
    window = np.hanning(block_size)
    transformed = np.fft.fft(blocks * window, axis=1)
    power = np.mean(np.abs(transformed) ** 2, axis=0)
    # Remove the mean-suppressed center and its immediate window neighbors.
    power = power[2:-1]
    total = float(np.sum(power))
    if total <= np.finfo(np.float64).tiny:
        return 0.0, 0.0
    normalized = power / total
    floor = max(np.finfo(np.float64).tiny, float(np.max(power)) * 1e-15)
    flatness = math.exp(float(np.mean(np.log(np.maximum(power, floor))))) \
               / float(np.mean(power))
    entropy = -float(np.sum(normalized * np.log(np.maximum(normalized, 1e-300)))) \
              / math.log(len(power))
    return _strength(flatness), _strength(entropy)


def _compact_spectrum(frequencies, power):
    edges = np.linspace(0, len(power), min(MAX_PERIOD_BINS, len(power)) + 1, dtype=int)
    total = np.concatenate(([0.0], np.cumsum(power)))
    compact = (total[edges[1:]] - total[edges[:-1]]) / np.diff(edges)
    centers = (frequencies[edges[:-1]] + frequencies[edges[1:] - 1]) / 2
    maximum = float(np.max(compact)) if len(compact) else 0.0
    relative = compact / maximum if maximum > 0 else np.zeros_like(compact)
    return {"frequency_hz": [_finite(value) for value in centers],
            "power_relative": [_finite(value) for value in relative]}


def analyze_structure(iq, sample_rate):
    """Return finite bounded measurements of the latest contiguous I/Q window.

    The maximum feature strength maps linearly to 0..100. Feature strengths use
    documented heuristic thresholds; they are not probabilities. Complex
    autocorrelation includes lag zero for drawing, while its peak metric excludes
    lag zero. The amplitude periodicity metric sums the strongest non-DC spectral
    bin and its immediate neighbors, divided by total non-DC envelope power.
    """
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, float)) \
            or not math.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("sample_rate must be positive and finite")
    values = np.asarray(iq)
    if values.ndim != 1 or not np.iscomplexobj(values):
        raise ValueError("IQ must be a one-dimensional complex array")
    data = np.asarray(values[-MAX_SAMPLES:], dtype=np.complex128)
    if not np.isfinite(data).all():
        raise ValueError("IQ contains non-finite samples")
    if len(data) and np.max(np.abs(data)) > 1e6:
        raise ValueError("IQ amplitude exceeds the supported measurement range")
    n = len(data)
    result = _empty(n, sample_rate, "insufficient-data")
    if n < 64:
        result["flags"] = [{"label": "Insufficient samples", "strength": 0.0,
                            "evidence": "At least 64 samples are required for these measurements"}]
        return result

    centered = data - np.mean(data)
    energy = float(np.sum(np.abs(centered) ** 2))
    raw_energy = float(np.sum(np.abs(data) ** 2))
    clipped = float(np.mean((np.abs(data.real) >= 127 / 128)
                           | (np.abs(data.imag) >= 127 / 128))) * 100
    result["metrics"]["clipped_percent"] = _finite(clipped)
    if energy <= np.finfo(np.float64).tiny or energy <= raw_energy * 1e-12:
        result.update(state="constant", structure_score=100.0)
        result["flags"] = [{"label": "Flatline / constant samples", "strength": 1.0,
                            "evidence": "Samples have no measurable variation; this can be DC, "
                                        "a stopped input, or an instrument artifact, not an RF message"}]
        return result

    result["state"] = "measured"
    flatness, entropy = _spectral_metrics(centered)
    lag_count = min(MAX_LAGS, n // 4 + 1)
    fft_size = 2 ** math.ceil(math.log2(2 * n))
    transformed = np.fft.fft(centered, n=fft_size)
    correlation = np.abs(np.fft.ifft(np.abs(transformed) ** 2)[:lag_count]) / energy
    correlation = np.clip(correlation, 0, 1)
    lag_peak = 1 + int(np.argmax(correlation[1:]))
    autocorrelation_peak = float(correlation[lag_peak])

    amplitudes = np.abs(data)
    amplitude_centered = amplitudes - np.mean(amplitudes)
    amplitude_variance = float(np.mean(amplitude_centered ** 2))
    amplitude_scale = float(np.mean(amplitudes ** 2))
    constant_envelope = amplitude_variance <= max(np.finfo(np.float64).tiny, amplitude_scale * 1e-12)
    if constant_envelope:
        kurtosis = 0.0  # Undefined for zero variance; availability is explicit.
        envelope_power = np.zeros(n // 2 + 1)
    else:
        kurtosis = float(np.mean(amplitude_centered ** 4) / amplitude_variance ** 2) - 3
        envelope_power = np.abs(np.fft.rfft(amplitude_centered * np.hanning(n))) ** 2
    envelope_power[0] = 0
    envelope_total = float(np.sum(envelope_power))
    period_bin = int(np.argmax(envelope_power)) if envelope_total > 0 else 0
    period_peak = float(np.sum(envelope_power[max(1, period_bin - 1):period_bin + 2])
                        / envelope_total) if envelope_total > 0 else 0.0
    frequencies = np.fft.rfftfreq(n, 1 / sample_rate)

    nonzero = amplitudes > 0
    phase_concentration = float(abs(np.mean(data[nonzero] / amplitudes[nonzero]))) \
                          if nonzero.any() else 0.0
    i_energy = float(np.sum(centered.real ** 2))
    q_energy = float(np.sum(centered.imag ** 2))
    iq_correlation = float(np.sum(centered.real * centered.imag) / math.sqrt(i_energy * q_energy)) \
                     if i_energy > 0 and q_energy > 0 else 0.0
    circularity = float(abs(np.sum(centered ** 2)) / energy)
    runs_z = _runs_z(centered.real)
    reference = _reference(n, lag_count - 1, len(envelope_power) - 1)
    thresholds = reference["thresholds"]
    flags = []
    _flag(flags, "Concentrated spectrum",
          f"Flatness {flatness:.3f}, normalized spectral entropy {entropy:.3f}; "
          "a carrier, filtering, or colored noise can concentrate energy",
          max((.65 - flatness) / .65, (.96 - entropy) / .35))
    correlation_limit = min(.99, thresholds["autocorrelation"])
    _flag(flags, "Correlated / colored signal",
          f"Complex autocorrelation peak {autocorrelation_peak:.3f} at "
          f"{lag_peak / sample_rate:.9g} s; dependence does not establish communication",
          (autocorrelation_peak - correlation_limit) / (1 - correlation_limit))
    period_limit = thresholds["periodicity_peak"]
    _flag(flags, "Repeated envelope" if period_bin >= 3 else "Slow envelope structure",
          f"Envelope peak near {frequencies[period_bin]:.6g} Hz contains "
          f"{period_peak * 100:.2f}% of non-DC envelope power in three bins",
          (period_peak - period_limit) / (.4 - period_limit))
    if constant_envelope:
        _flag(flags, "Constant envelope",
              "Amplitude has no measurable variance; this differs from Gaussian-noise "
              "amplitude even when phase and spectrum look noise-like", 1)
    else:
        _flag(flags, "Non-Gaussian amplitude",
              f"Amplitude excess kurtosis {kurtosis:.3f}; circular Gaussian noise has "
              f"Rayleigh amplitude with reference excess kurtosis {RAYLEIGH_EXCESS_KURTOSIS:.3f}",
              (abs(kurtosis - RAYLEIGH_EXCESS_KURTOSIS) - .75) / 4)
    phase_limit = min(.99, thresholds["phase_concentration"])
    _flag(flags, "Preferred phase",
          f"Mean unit-phase vector magnitude {phase_concentration:.3f}; DC and "
          "receiver imbalance can bias phase as well as signals",
          (phase_concentration - phase_limit) / (1 - phase_limit))
    iq_limit = min(.99, thresholds["i_q_correlation"])
    circularity_limit = min(.99, thresholds["circularity_coefficient"])
    _flag(flags, "I/Q dependence or imbalance",
          f"I/Q correlation {iq_correlation:.3f}, circularity coefficient {circularity:.3f}; "
          "a noncircular waveform or receiver imbalance can produce this",
          max((abs(iq_correlation) - iq_limit) / (1 - iq_limit),
              (circularity - circularity_limit) / (1 - circularity_limit)))
    _flag(flags, "Unusual sign runs",
          f"In-phase sign-run z {runs_z:.3f}; the reference assumes independent signs, "
          "and is not a calibrated significance claim for correlated samples",
          (abs(runs_z) - 6) / 24)
    _flag(flags, "Near digital full scale",
          f"{clipped:.3f}% of samples have an I or Q component near full scale; "
          "possible clipping or unsuitable normalization",
          (clipped - .2) / 5)
    flags.sort(key=lambda flag: flag["strength"], reverse=True)
    result.update(
        structure_score=_finite(100 * max((flag["strength"] for flag in flags), default=0)),
        metrics={
            "spectral_flatness": _finite(flatness),
            "normalized_spectral_entropy": _finite(entropy),
            "complex_autocorrelation_peak": _finite(autocorrelation_peak),
            "complex_autocorrelation_lag_seconds": _finite(lag_peak / sample_rate),
            "periodicity_peak": _finite(period_peak),
            "periodicity_hz": _finite(frequencies[period_bin]),
            "amplitude_excess_kurtosis": _finite(kurtosis),
            "amplitude_kurtosis_available": not constant_envelope,
            "phase_concentration": _finite(phase_concentration),
            "i_q_correlation": _finite(np.clip(iq_correlation, -1, 1)),
            "circularity_coefficient": _finite(_strength(circularity)),
            "sign_runs_z": _finite(runs_z),
            "clipped_percent": _finite(clipped),
        },
        flags=flags,
        autocorrelation={"lags_seconds": [_finite(value / sample_rate) for value in range(lag_count)],
                         "values": [_finite(value) for value in correlation]},
        periodicity_spectrum=_compact_spectrum(frequencies, envelope_power),
        reference=reference,
    )
    return result
