"""Offline protocol recognition and bounded payload-format inspection.

The optional pinned rtl_433 executable in tools/rtl_433 is the file-only build.
Every decode command has -r and -c 0: it cannot select an SDR or load a user's
configuration that opens one. Recognized telemetry is not proof of its origin.
This module does not decrypt, transmit, or access receiver hardware.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
import time
import zlib

import numpy as np


MAX_IQ_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
MAX_RECORDS = 128
MAX_PAYLOAD_BYTES = 64 * 1024
RTL_433_VERSION = "25.12"
RTL_433_SHA256 = "7a72a4b0b282e52e1444686c7ff379e5ce4bdeeeaa5183e686f3f5d0d4516bd6"
RTL_433_ARCHIVE_SHA256 = "088a00aa5446c8f859346a93320fb2ae353b7689a1c53b4a5bb20df0ff1c1151"
RTL_433_RELEASE_URL = "https://github.com/merbanan/rtl_433/releases/tag/25.12"
RTL_433_SOURCE_URL = "https://github.com/merbanan/rtl_433/tree/25.12"
COMMON_BANDS = ((168_000_000, 170_000_000), (300_000_000, 350_000_000),
                (420_000_000, 450_000_000), (860_000_000, 930_000_000))


def _finite(value):
    """Bound nested decoder fields before returning them as trusted UI data."""
    def clean(item, depth=0):
        if depth > 5:
            return "[depth limit]"
        if item is None or isinstance(item, bool):
            return item
        if isinstance(item, int):
            return item if abs(item) <= (1 << 63) - 1 else str(item)[:128]
        if isinstance(item, float):
            return item if math.isfinite(item) else None
        if isinstance(item, str):
            return item[:4096]
        if isinstance(item, dict):
            return {str(key)[:128]: clean(data, depth + 1)
                    for key, data in list(item.items())[:64]}
        if isinstance(item, list):
            return [clean(data, depth + 1) for data in item[:64]]
        return str(item)[:512]
    return clean(value)


def _text_preview(data):
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    printable = sum(char.isprintable() or char in "\r\n\t" for char in text)
    if not text or printable / len(text) < .9:
        return None
    return {"text": text[:4096], "text_truncated": len(text) > 4096,
            "printable_fraction": round(printable / len(text), 5)}


def _reject_constant(_value):
    raise ValueError("Non-standard JSON numeric constant")


def inspect_payload(payload):
    """Recognize byte encodings/signatures without equating them with messages.

    Only one transformation level is attempted. gzip has a hard decompressed
    size limit; base64 and hex are strict candidates, not cryptographic work.
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError("payload must be bytes-like")
    original_size = len(payload)
    data = bytes(payload[:MAX_PAYLOAD_BYTES])
    counts = np.bincount(np.frombuffer(data, dtype=np.uint8), minlength=256)
    probabilities = counts[counts > 0] / max(1, len(data))
    entropy = float(-np.sum(probabilities * np.log2(probabilities))) if len(data) else 0.0
    findings = []
    preview = _text_preview(data)
    if preview:
        findings.append({"format": "ASCII" if data.isascii() else "UTF-8", "kind": "text",
                         "confidence": "valid-encoding", **preview,
                         "detail": "Valid printable text encoding; meaning and origin are not verified"})
        try:
            parsed = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
            if isinstance(parsed, (dict, list)):
                findings.append({"format": "JSON", "kind": "structured-data",
                                 "confidence": "valid-syntax", "value": _finite(parsed),
                                 "detail": "JSON syntax parses; this is not authentication or decryption"})
        except (ValueError, RecursionError):
            pass
        compact = re.sub(rb"\s+", b"", data)
        if len(compact) >= 16 and len(compact) % 4 == 0:
            try:
                decoded = base64.b64decode(compact, validate=True)
                if base64.b64encode(decoded).rstrip(b"=") == compact.rstrip(b"="):
                    findings.append({"format": "base64", "kind": "encoding-candidate",
                                     "confidence": "strict-syntax", "decoded_bytes": len(decoded),
                                     "decoded_hex_preview": decoded[:128].hex(),
                                     "decoded_text": (_text_preview(decoded) or {}).get("text"),
                                     "detail": "Reversible encoding candidate; some ordinary text also matches base64"})
            except (ValueError, binascii.Error):
                pass
        hexadecimal = compact[2:] if compact.lower().startswith(b"0x") else compact
        if len(hexadecimal) >= 8 and len(hexadecimal) % 2 == 0 \
                and re.fullmatch(rb"[0-9a-fA-F]+", hexadecimal):
            decoded = bytes.fromhex(hexadecimal.decode("ascii"))
            findings.append({"format": "hex", "kind": "encoding-candidate",
                             "confidence": "strict-syntax", "decoded_bytes": len(decoded),
                             "decoded_text": (_text_preview(decoded) or {}).get("text"),
                             "decoded_hex_preview": decoded[:128].hex(),
                             "detail": "Hexadecimal byte encoding candidate, not encryption"})
    signatures = ((b"\x1f\x8b", "gzip"), (b"PK\x03\x04", "ZIP"),
                  (b"\x89PNG\r\n\x1a\n", "PNG"), (b"\xff\xd8\xff", "JPEG"),
                  (b"%PDF-", "PDF"), (b"\x7fELF", "ELF"),
                  (b"\xd4\xc3\xb2\xa1", "PCAP"), (b"\x0a\x0d\x0d\x0a", "PCAP-NG"))
    for prefix, name in signatures:
        if data.startswith(prefix):
            finding = {"format": name, "kind": "file-signature", "confidence": "signature-only",
                       "detail": "Recognized leading byte signature; complete file integrity has not been checked"}
            if name == "gzip":
                try:
                    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
                    unpacked = decompressor.decompress(data, MAX_PAYLOAD_BYTES + 1)
                    limited = len(unpacked) > MAX_PAYLOAD_BYTES or bool(decompressor.unconsumed_tail)
                    finding.update(decoded_bytes=min(len(unpacked), MAX_PAYLOAD_BYTES),
                                   decompressed_truncated=limited,
                                   complete=bool(decompressor.eof and not limited),
                                   decoded_text=(_text_preview(unpacked[:MAX_PAYLOAD_BYTES]) or {}).get("text"))
                    if finding["complete"]:
                        finding["confidence"] = "valid-gzip-stream"
                except zlib.error:
                    finding["complete"] = False
                    finding["detail"] = "gzip signature found, but decompression failed"
            findings.append(finding)
    return {"input_bytes": original_size, "inspected_bytes": len(data),
            "input_truncated": original_size > MAX_PAYLOAD_BYTES,
            "entropy_bits_per_byte": round(entropy, 5), "formats": findings,
            "hex_preview": data[:128].hex(),
            "interpretation": "Format recognition and reversible decoding only. High entropy does not prove encryption; "
                              "readable data is not proof of a message's origin."}


class AutomaticDecoders:
    """Finite offline rtl_433 runs plus a code-defined decoder extension registry."""

    def __init__(self, tools_dir=None, enabled=True, timeout_seconds=4):
        directory = Path(tools_dir) if tools_dir is not None else Path(__file__).resolve().parent / "tools" / "rtl_433"
        self.executable = (directory / "rtl_433.exe").resolve()
        self.enabled = bool(enabled)
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) \
                or not math.isfinite(timeout_seconds) or not .1 <= timeout_seconds <= 4:
            raise ValueError("timeout_seconds must be in 0.1..4")
        self.timeout_seconds = float(timeout_seconds)
        self._probe_result = None
        self._last_attempt = {"state": "not-run", "records": 0, "error": None}
        self._extensions = {}

    def register(self, module_id, label, handler):
        """Extensions are local Python callables, never command strings or paths.

        A caller adding a decoder is responsible for keeping its callable
        finite. No HTTP input in this project can register or execute plugins.
        """
        if not isinstance(module_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,48}", module_id):
            raise ValueError("invalid decoder module id")
        if module_id in self._extensions or module_id in {"rtl_433", "payload_formats"}:
            raise ValueError("decoder module id already exists")
        if not callable(handler):
            raise TypeError("decoder extension must be a local Python callable")
        self._extensions[module_id] = {"label": str(label)[:128], "handler": handler}

    def _execute(self, arguments, timeout=None):
        # Redirect stdout to a temporary file so neither communicate nor an
        # exceptionally chatty external decoder can allocate unbounded memory.
        with tempfile.TemporaryDirectory(prefix="anomaly-sdr-offline-decode-") as temporary:
            output_path = Path(temporary) / "stdout.txt"
            error_path = Path(temporary) / "stderr.txt"
            with output_path.open("wb") as output, error_path.open("wb") as error:
                completed = subprocess.run([str(self.executable), *arguments],
                                           stdin=subprocess.DEVNULL, stdout=output, stderr=error,
                                           cwd=temporary, timeout=timeout or self.timeout_seconds,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                           check=False)
            with output_path.open("rb") as output:
                stdout = output.read(MAX_OUTPUT_BYTES)
            with error_path.open("rb") as error:
                stderr = error.read(8192)
            return completed.returncode, stdout, stderr, output_path.stat().st_size > MAX_OUTPUT_BYTES

    def _probe(self):
        if self._probe_result is not None:
            return self._probe_result
        result = {"id": "rtl_433", "name": "rtl_433", "label": "Known OOK/FSK telemetry protocols",
                  "enabled": self.enabled, "available": False, "state": "unavailable",
                  "protocol_count": 0, "default_protocol_count": 0, "version": None,
                  "license": "GPL-2.0-or-later", "release_url": RTL_433_RELEASE_URL,
                  "source_url": RTL_433_SOURCE_URL, "offline_only": True,
                  "detail": "Install the pinned official file-only Windows build in tools/rtl_433"}
        if not self.enabled:
            result.update(state="disabled", detail="Offline external protocol decoder is disabled")
        elif self.executable.is_file():
            try:
                digest = hashlib.sha256(self.executable.read_bytes()).hexdigest()
                if digest != RTL_433_SHA256:
                    result["detail"] = "Executable hash does not match the pinned official rtl_433 25.12 file-only build"
                else:
                    code, output, error, _ = self._execute(["-V"], timeout=2)
                    text = (output + error).decode("utf-8", "replace")
                    if code == 0 and "inputs file rtl_tcp" in text and "RTL-SDR" not in text and "SoapySDR" not in text:
                        _, listing, errors, _ = self._execute(["-R", "help"], timeout=2)
                        listing = (listing + errors).decode("utf-8", "replace")
                        protocols = re.findall(r"^\s*\[(\d+)\](\*)?", listing, flags=re.MULTILINE)
                        result.update(available=True, state="ready", version=RTL_433_VERSION,
                                      protocol_count=len(protocols),
                                      default_protocol_count=sum(not disabled for _, disabled in protocols),
                                      detail="Pinned file-only build; recognized protocols have different integrity checks. "
                                             "Common telemetry bands only; this is not a universal RF decoder")
                    else:
                        result["detail"] = "File-only decoder version probe did not succeed"
            except (OSError, subprocess.TimeoutExpired) as error:
                result["detail"] = f"Offline decoder unavailable: {type(error).__name__}"
        self._probe_result = result
        return result

    def capabilities(self):
        modules = [copy.deepcopy(self._probe()),
                   {"id": "payload_formats", "name": "Payload inspector", "label": "Text and byte formats",
                    "available": True, "enabled": True, "state": "ready",
                    "detail": "ASCII/UTF-8, JSON, strict base64/hex candidates, bounded gzip, file signatures; no decryption"}]
        for name, extension in self._extensions.items():
            modules.append({"id": name, "name": extension["label"], "label": extension["label"],
                            "available": True, "enabled": True, "state": "ready",
                            "detail": "Locally registered Python decoder extension"})
        return {"modules": modules, "formats": ["ASCII", "UTF-8", "JSON", "base64", "hex", "gzip"],
                "last_attempt": copy.deepcopy(self._last_attempt),
                "scope": "Known telemetry and explicit formats; other signals remain unknown",
                "common_bands_hz": [list(band) for band in COMMON_BANDS]}

    def _records(self, output, sample_rate, center_hz):
        records = []
        for line in output.splitlines():
            if len(line) > 32 * 1024:
                continue
            try:
                source = json.loads(line.decode("utf-8"), parse_constant=lambda _value: None)
            except (ValueError, UnicodeDecodeError, RecursionError):
                continue
            if not isinstance(source, dict) or not isinstance(source.get("model"), str) or not source["model"]:
                continue
            # rtl_433 names integrity metadata 'mic'. Many protocols have no
            # CRC at all; recognizing one must not create a false CRC receipt.
            integrity = str(source.get("mic", "not-reported"))[:64]
            reported = integrity.upper() in {"CRC", "CHECKSUM"}
            fields = _finite(source)
            record = {"decoder": "rtl_433", "protocol": fields.get("protocol"),
                      "model": fields["model"], "fields": fields,
                      "recognized": True, "verified": reported,
                      "validation": "rtl_433 recognized protocol",
                      "integrity": integrity, "integrity_validated": reported,
                      "crc_valid": True if integrity.upper() == "CRC" else None,
                      "source_origin_verified": False, "frequency_hz": center_hz,
                      "sample_rate": sample_rate,
                      "detail": "Protocol integrity check reported by decoder" if reported
                                else "Protocol recognized; no CRC/checksum result was reported"}
            records.append(record)
            if len(records) >= MAX_RECORDS:
                break
        return records

    def decode(self, raw_iq, sample_rate, center_hz):
        """Return recognized records from a finite signed-int8 IQ window.

        Raw capture is converted to rtl_433's unsigned cu8 representation.
        The default decoder list excludes its experimental disabled protocols.
        An empty result means no recognized record in this bounded attempt.
        """
        if not isinstance(raw_iq, (bytes, bytearray, memoryview)):
            raise TypeError("raw_iq must be signed-int8 interleaved bytes")
        if len(raw_iq) % 2:
            raise ValueError("raw_iq must contain complete I/Q pairs")
        for name, value in (("sample_rate", sample_rate), ("center_hz", center_hz)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if sample_rate > 20_000_000 or center_hz > 6_000_000_000:
            raise ValueError("sample rate or center frequency is outside the supported capture range")
        band_supported = any(low <= center_hz <= high for low, high in COMMON_BANDS)
        attempt = {"state": "not-run", "records": 0, "error": None,
                   "input_bytes": len(raw_iq), "input_truncated": len(raw_iq) > MAX_IQ_BYTES,
                   "band_supported": band_supported, "sample_rate": sample_rate,
                   "center_hz": center_hz}
        records = []
        if not band_supported:
            attempt.update(state="outside-known-bands",
                           detail="Telemetry decoder is not applied here; other protocols remain unknown")
        elif len(raw_iq) < 1024:
            attempt.update(state="short-window", detail="Too few IQ samples for this offline attempt")
        elif not self._probe()["available"]:
            attempt.update(state=self._probe()["state"], detail=self._probe()["detail"])
        else:
            bounded = bytes(raw_iq[-MAX_IQ_BYTES:])
            signed = np.frombuffer(bounded, dtype=np.int8).astype(np.int16)
            unsigned = (signed + 128).astype(np.uint8)
            start = time.monotonic()
            try:
                with tempfile.TemporaryDirectory(prefix="anomaly-sdr-iq-window-") as temporary:
                    filename = Path(temporary) / "input.cu8"
                    filename.write_bytes(unsigned.tobytes())
                    args = ["-c", "0", "-r", str(filename), "-s", str(int(sample_rate)),
                            "-f", str(int(center_hz)), "-n", str(len(bounded) // 2),
                            "-F", "json", "-M", "protocol", "-M", "level", "-M", "time:iso:utc"]
                    code, output, error, truncated = self._execute(args)
                    records = self._records(output, sample_rate, center_hz)
                    attempt.update(state="recognized" if records else "no-recognized-record",
                                   records=len(records), output_truncated=truncated, exit_code=code,
                                   detail="No recognized record is not proof that a signal contains no data")
                    if code != 0:
                        records = []
                        attempt.update(state="decoder-error", records=0,
                                       error=error.decode("utf-8", "replace")[:2048])
            except subprocess.TimeoutExpired:
                attempt.update(state="timeout", error="Offline protocol decoder exceeded its four-second limit")
            except OSError as error:
                attempt.update(state="decoder-error", error=f"{type(error).__name__}: {str(error)[:512]}")
            attempt["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)
        # Extension registration is code-only. An extension sees a bounded byte
        # window and metadata; recognized output stays bounded and sanitized.
        for module_id, extension in self._extensions.items():
            try:
                output = extension["handler"](bytes(raw_iq[-MAX_IQ_BYTES:]), sample_rate, center_hz)
                if isinstance(output, list):
                    for item in output[:MAX_RECORDS - len(records)]:
                        if isinstance(item, dict):
                            records.append({**_finite(item), "decoder": module_id})
            except (ValueError, TypeError, RuntimeError):
                attempt["extension_error"] = "A locally registered decoder did not complete successfully"
        attempt["records"] = len(records[:MAX_RECORDS])
        self._last_attempt = attempt
        return records[:MAX_RECORDS]

