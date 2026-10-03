"""Finite offline recognition tests; external integration never opens SDRs."""

import base64
import gzip
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np

from auto_decoders import (AutomaticDecoders, MAX_IQ_BYTES, MAX_PAYLOAD_BYTES,
                           MAX_RECORDS, inspect_payload)


ROOT = Path(__file__).resolve().parent
TOOL = ROOT / "tools" / "rtl_433" / "rtl_433.exe"


def synthetic_nexus_iq():
    """Original software-generated test data, with no recorded radio traffic.

    Field layout and PPM timing are documented by the upstream decoder:
    https://github.com/merbanan/rtl_433/blob/25.12/src/devices/nexus.c
    The frame describes ID 181, channel 2, 19.0 C and 71 percent humidity.
    """
    bits = f'{0xB590BEF47:036b}'
    envelope = [np.zeros(12_500, dtype=np.float32)]
    for _ in range(12):
        for bit in bits:
            envelope.extend((np.ones(125, dtype=np.float32),
                             np.zeros(500 if bit == '1' else 250, dtype=np.float32)))
        envelope.extend((np.ones(125, dtype=np.float32), np.zeros(1_000, dtype=np.float32)))
    envelope.append(np.zeros(5_000, dtype=np.float32))
    level = np.concatenate(envelope) * 46
    phase = 2 * np.pi * 40_000 * np.arange(len(level)) / 250_000
    return np.rint(np.column_stack((level * np.cos(phase), level * np.sin(phase)))).astype(np.int8).tobytes()


class StubDecoder(AutomaticDecoders):
    """Capture the actual finite command and converted input without a radio."""
    def __init__(self, output=b"", code=0, failure=None):
        super().__init__()
        self.calls = []
        self.output = output
        self.code = code
        self.failure = failure
        self._probe_result = {"available": True, "state": "ready", "id": "rtl_433",
                              "name": "rtl_433", "detail": "offline test executable"}

    def _execute(self, arguments, timeout=None):
        filename = Path(arguments[arguments.index("-r") + 1])
        self.calls.append({"arguments": list(arguments), "data": filename.read_bytes()})
        if self.failure:
            raise self.failure
        return self.code, self.output, b"offline test diagnostic", False


class PayloadFormatTests(unittest.TestCase):
    def formats(self, payload):
        return {item["format"]: item for item in inspect_payload(payload)["formats"]}

    def test_printable_ascii_and_utf8(self):
        self.assertIn("ASCII", self.formats(b"ASDR-LAB|v1|SEQ=000001"))
        self.assertIn("UTF-8", self.formats("temperature 19°C".encode()))
        self.assertEqual(self.formats(b"\xff\x00\x9a"), {})

    def test_json_syntax_without_authentication_claim(self):
        formats = self.formats(b'{"sensor": 7, "temperature": 19.25}')
        self.assertEqual(formats["JSON"]["value"]["temperature"], 19.25)
        self.assertIn("not authentication", formats["JSON"]["detail"])
        self.assertNotIn("JSON", self.formats(b'{"broken":'))
        self.assertNotIn("JSON", self.formats(b'{"not-standard": NaN}'))

    def test_base64_and_hex_are_reversible_encoding_candidates(self):
        original = b"Own lab message with primes 2 3 5 7"
        base64_result = self.formats(base64.b64encode(original))["base64"]
        self.assertEqual(base64_result["decoded_text"], original.decode())
        self.assertEqual(base64_result["kind"], "encoding-candidate")
        hex_result = self.formats(original.hex().encode())["hex"]
        self.assertEqual(hex_result["decoded_text"], original.decode())
        self.assertNotIn("base64", self.formats(b"@@@@ invalid base64@@@@"))

    def test_gzip_stream_is_bounded_and_crc_checked_by_zlib(self):
        compressed = gzip.compress(b"Own lab message", mtime=0)
        result = self.formats(compressed)["gzip"]
        self.assertTrue(result["complete"])
        self.assertEqual(result["decoded_text"], "Own lab message")
        broken = compressed[:-3] + b"BAD"
        self.assertFalse(self.formats(broken)["gzip"]["complete"])

    def test_gzip_bomb_does_not_allocate_its_full_output(self):
        bomb = gzip.compress(b"A" * (MAX_PAYLOAD_BYTES * 20), mtime=0)
        result = self.formats(bomb)["gzip"]
        self.assertTrue(result["decompressed_truncated"])
        self.assertEqual(result["decoded_bytes"], MAX_PAYLOAD_BYTES)
        self.assertFalse(result["complete"])

    def test_file_magic_is_only_a_signature_claim(self):
        result = self.formats(b"\x89PNG\r\n\x1a\nnot-a-valid-image")["PNG"]
        self.assertEqual(result["confidence"], "signature-only")

    def test_large_payload_and_deep_json_outputs_are_bounded(self):
        result = inspect_payload(b"A" * (MAX_PAYLOAD_BYTES + 50))
        self.assertTrue(result["input_truncated"])
        self.assertEqual(result["inspected_bytes"], MAX_PAYLOAD_BYTES)
        self.assertLess(len(json.dumps(result, allow_nan=False)), 20_000)
        result = inspect_payload(b"[" * 2000 + b"0" + b"]" * 2000)
        json.dumps(result, allow_nan=False)

    def test_entropy_does_not_claim_encryption(self):
        low = inspect_payload(b"a" * 1000)
        high = inspect_payload(bytes(range(256)) * 4)
        self.assertEqual(low["entropy_bits_per_byte"], 0)
        self.assertEqual(high["entropy_bits_per_byte"], 8)
        self.assertIn("does not prove encryption", high["interpretation"])

    def test_empty_and_non_bytes_inputs(self):
        result = inspect_payload(b"")
        self.assertEqual(result["formats"], [])
        self.assertEqual(result["entropy_bits_per_byte"], 0)
        with self.assertRaises(TypeError):
            inspect_payload("this is not bytes")


class OfflineDecoderTests(unittest.TestCase):
    def test_signed_to_unsigned_conversion_and_offline_command(self):
        decoder = StubDecoder(b'{"protocol":19,"model":"Nexus-TH","temperature_C":19}\n')
        raw = bytes([0, 127, 128, 255]) * 1024
        records = decoder.decode(raw, 250_000, 433_920_000)
        call = decoder.calls[0]
        self.assertEqual(call["data"][:4], bytes([128, 255, 0, 127]))
        args = call["arguments"]
        self.assertEqual(args[args.index("-c") + 1], "0")
        self.assertIn("-r", args)
        self.assertNotIn("-d", args)
        self.assertEqual(args[args.index("-n") + 1], str(len(raw) // 2))
        self.assertEqual(records[0]["model"], "Nexus-TH")
        self.assertFalse(records[0]["verified"])
        self.assertIsNone(records[0]["crc_valid"])
        self.assertFalse(records[0]["source_origin_verified"])

    def test_integrity_receipts_are_not_invented(self):
        decoder = StubDecoder(b'{"model":"CRC-device","mic":"CRC"}\n'
                              b'{"model":"checksum-device","mic":"CHECKSUM"}\n'
                              b'{"model":"unknown-integrity"}\n')
        records = decoder.decode(b"\0" * 2048, 250_000, 433_920_000)
        self.assertTrue(records[0]["crc_valid"])
        self.assertTrue(records[0]["verified"])
        self.assertTrue(records[1]["verified"])
        self.assertIsNone(records[1]["crc_valid"])
        self.assertFalse(records[2]["verified"])

    def test_outside_known_bands_never_runs_external_decoder(self):
        decoder = StubDecoder()
        self.assertEqual(decoder.decode(b"\0" * 2048, 8_000_000, 1_600_000_000), [])
        self.assertEqual(decoder.calls, [])
        self.assertEqual(decoder.capabilities()["last_attempt"]["state"], "outside-known-bands")

    def test_short_input_unavailable_disabled_are_truthful(self):
        decoder = StubDecoder()
        self.assertEqual(decoder.decode(b"\0" * 100, 250_000, 433_920_000), [])
        self.assertEqual(decoder.calls, [])
        self.assertEqual(decoder.capabilities()["last_attempt"]["state"], "short-window")
        with tempfile.TemporaryDirectory() as temporary:
            decoder = AutomaticDecoders(tools_dir=temporary)
            self.assertFalse(decoder.capabilities()["modules"][0]["available"])
            self.assertEqual(decoder.decode(b"\0" * 2048, 250_000, 433_920_000), [])
            self.assertEqual(decoder.capabilities()["last_attempt"]["state"], "unavailable")
        decoder = AutomaticDecoders(enabled=False)
        self.assertEqual(decoder.capabilities()["modules"][0]["state"], "disabled")

    def test_modified_external_executable_is_not_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / "rtl_433.exe").write_bytes(b"not the official binary")
            decoder = AutomaticDecoders(tools_dir=temporary)
            self.assertFalse(decoder.capabilities()["modules"][0]["available"])
            self.assertIn("hash", decoder.capabilities()["modules"][0]["detail"])

    def test_large_input_is_capped_and_window_truncation_reported(self):
        decoder = StubDecoder()
        decoder.decode(b"\0" * (MAX_IQ_BYTES + 1024), 250_000, 433_920_000)
        self.assertEqual(len(decoder.calls[0]["data"]), MAX_IQ_BYTES)
        self.assertTrue(decoder.capabilities()["last_attempt"]["input_truncated"])

    def test_output_limits_invalid_lines_and_nonfinite_fields(self):
        lines = [b"not json", b'{"log":"no model"}',
                 b'{"model":"test","measurement":NaN,"long":' + b"9" * 2000 + b"}"]
        lines.extend([b'{"model":"test","mic":"CRC"}'] * (MAX_RECORDS + 20))
        decoder = StubDecoder(b"\n".join(lines))
        records = decoder.decode(b"\0" * 2048, 250_000, 433_920_000)
        self.assertEqual(len(records), MAX_RECORDS)
        self.assertIsNone(records[0]["fields"]["measurement"])
        json.dumps(records, allow_nan=False)

    def test_timeout_and_errors_do_not_become_decoded_records(self):
        decoder = StubDecoder(failure=subprocess.TimeoutExpired("offline-decoder", 4))
        self.assertEqual(decoder.decode(b"\0" * 2048, 250_000, 433_920_000), [])
        self.assertEqual(decoder.capabilities()["last_attempt"]["state"], "timeout")
        decoder = StubDecoder(b'{"model":"partial-record","mic":"CRC"}', code=1)
        self.assertEqual(decoder.decode(b"\0" * 2048, 250_000, 433_920_000), [])
        self.assertEqual(decoder.capabilities()["last_attempt"]["state"], "decoder-error")

    def test_unknown_is_not_relabelled_as_no_data(self):
        decoder = StubDecoder()
        self.assertEqual(decoder.decode(b"\0" * 2048, 250_000, 433_920_000), [])
        attempt = decoder.capabilities()["last_attempt"]
        self.assertEqual(attempt["state"], "no-recognized-record")
        self.assertIn("not proof", attempt["detail"])

    def test_invalid_metadata_or_partial_iq_is_rejected(self):
        decoder = StubDecoder()
        for raw, rate, center in [(b"x", 250_000, 433_920_000),
                                  (b"\0" * 2048, 0, 433_920_000),
                                  (b"\0" * 2048, 250_000, float("nan")),
                                  (b"\0" * 2048, True, 433_920_000),
                                  (b"\0" * 2048, 40_000_000, 433_920_000)]:
            with self.assertRaises(ValueError):
                decoder.decode(raw, rate, center)
        with self.assertRaises(TypeError):
            decoder.decode("not bytes", 250_000, 433_920_000)
        for timeout in [0, 5, float("nan"), True]:
            with self.assertRaises(ValueError):
                AutomaticDecoders(timeout_seconds=timeout)

    def test_extension_registry_is_local_callable_only_and_bounded(self):
        decoder = AutomaticDecoders(enabled=False)
        decoder.register("own_lab", "Owned lab protocol", lambda data, rate, center:
                         [{"recognized": True, "verified": False, "input_size": len(data)}])
        records = decoder.decode(b"\0" * 2048, 250_000, 433_920_000)
        self.assertEqual(records[0]["decoder"], "own_lab")
        self.assertEqual(records[0]["input_size"], 2048)
        self.assertTrue(any(module["id"] == "own_lab" for module in decoder.capabilities()["modules"]))
        with self.assertRaises(TypeError):
            decoder.register("arbitrary", "No executable strings", "cmd.exe")
        with self.assertRaises(ValueError):
            decoder.register("own_lab", "Duplicate", lambda *args: [])

    @unittest.skipUnless(TOOL.is_file(), "Optional official rtl_433 binary absent")
    def test_synthetic_sensor_is_actually_decoded_by_file_only_binary(self):
        signed = synthetic_nexus_iq()
        decoder = AutomaticDecoders()
        capability = decoder.capabilities()["modules"][0]
        self.assertTrue(capability["offline_only"])
        self.assertEqual(capability["version"], "25.12")
        self.assertGreater(capability["protocol_count"], 250)
        records = decoder.decode(signed, 250_000, 433_920_000)
        self.assertTrue(records)
        nexus = next(record for record in records if record["model"] == "Nexus-TH")
        self.assertEqual(nexus["protocol"], 19)
        self.assertEqual(nexus["fields"]["temperature_C"], 19)
        self.assertEqual(nexus["fields"]["humidity"], 71)
        self.assertEqual(nexus["fields"]["id"], 181)
        self.assertFalse(nexus["verified"])
        self.assertIsNone(nexus["crc_valid"])

    @unittest.skipUnless(TOOL.is_file(), "Optional official rtl_433 binary absent")
    def test_real_offline_zero_input_does_not_generate_telemetry(self):
        decoder = AutomaticDecoders()
        self.assertEqual(decoder.decode(b"\0" * 100_000, 250_000, 433_920_000), [])
        self.assertEqual(decoder.capabilities()["last_attempt"]["state"], "no-recognized-record")


if __name__ == "__main__":
    unittest.main()
