"""Offline packet checks: no SDR access and no RF transmission."""

from datetime import datetime, timezone
import unittest
import zlib

from prime_packets import PACKET_NOTICE, build_packet, decode_packet


class PrimePacketTests(unittest.TestCase):
    def test_roundtrip_boundary_and_default_prime_counts(self):
        expected_first_16 = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53]
        for sequence, count in ((1, 1), (12345, 16), (999999, 64)):
            with self.subTest(sequence=sequence, prime_count=count):
                packet = build_packet(sequence, count)
                decoded = decode_packet(bytes.fromhex(packet["payload_hex"]))
                self.assertEqual(decoded["sequence"], sequence)
                self.assertEqual(decoded["prime_count"], count)
                self.assertEqual(decoded["primes"][: min(count, 16)], expected_first_16[:count])
                self.assertEqual(decoded["payload_text"], packet["payload_text"])
                self.assertEqual(decoded["crc32"], packet["crc32"])
                self.assertEqual(decode_packet(packet["payload_text"]), decoded)
                if count == 64:
                    self.assertEqual(decoded["primes"][-1], 311)

    def test_wire_format_and_crc_scope(self):
        packet = build_packet(1, 1)
        body = b"ASDR-LAB|v1|SEQ=000001|PRIMES=2"
        expected_checksum = f"{zlib.crc32(body) & 0xFFFFFFFF:08X}"
        expected_frame = body + f"|CRC32={expected_checksum}\n".encode("ascii")
        self.assertEqual(packet["payload_text"].encode("utf-8"), expected_frame)
        self.assertEqual(packet["payload_hex"], expected_frame.hex())
        self.assertEqual(
            set(packet),
            {"sequence", "payload_text", "payload_hex", "crc32", "prime_count", "created_utc"},
        )
        created = datetime.fromisoformat(packet["created_utc"].replace("Z", "+00:00"))
        self.assertEqual(created.utcoffset(), timezone.utc.utcoffset(created))
        self.assertIn("Local laboratory", PACKET_NOTICE)
        self.assertIn("not authenticated", PACKET_NOTICE)

    def test_corruption_fails_crc(self):
        text = build_packet(1, 16)["payload_text"]
        for damaged in (
            text.replace("SEQ=000001", "SEQ=000002"),
            text.replace("PRIMES=2,3", "PRIMES=2,4"),
            text.rsplit("|CRC32=", 1)[0] + "|CRC32=00000000\n",
        ):
            with self.subTest(damaged=damaged):
                with self.assertRaisesRegex(ValueError, "CRC32 mismatch"):
                    decode_packet(damaged)

    def test_corrupted_framing_fails(self):
        text = build_packet(1, 1)["payload_text"]
        for damaged in (
            text.replace("ASDR-LAB", "ASDR-OTHER"),
            text.replace("|v1|", "|v2|"),
            text.replace("SEQ=000001", "SEQ=1"),
            text[:-1],
            text + "trailing data",
            text.replace("\n", "\r\n"),
            text.replace("CRC32=", "CHECKSUM="),
        ):
            with self.subTest(damaged=damaged):
                with self.assertRaisesRegex(ValueError, "framing"):
                    decode_packet(damaged)

    def test_valid_crc_does_not_make_invalid_primes_valid(self):
        for prime_text in ("2,4", "02,3", "3,5", "2,3,3", ",".join(["2"] * 65)):
            with self.subTest(prime_text=prime_text):
                body = f"ASDR-LAB|v1|SEQ=000001|PRIMES={prime_text}".encode("ascii")
                checksum = f"{zlib.crc32(body) & 0xFFFFFFFF:08X}"
                with self.assertRaises(ValueError):
                    decode_packet(body + f"|CRC32={checksum}\n".encode("ascii"))

    def test_invalid_prime_counts(self):
        for count in (0, -1, 65, 1.5, "16", True, False, None):
            with self.subTest(prime_count=count):
                with self.assertRaisesRegex(ValueError, "prime_count"):
                    build_packet(1, count)

    def test_invalid_sequences(self):
        for sequence in (0, -1, 1000000, 1.5, "1", True, False, None):
            with self.subTest(sequence=sequence):
                with self.assertRaisesRegex(ValueError, "sequence"):
                    build_packet(sequence, 1)
        body = b"ASDR-LAB|v1|SEQ=000000|PRIMES=2"
        checksum = f"{zlib.crc32(body) & 0xFFFFFFFF:08X}"
        with self.assertRaisesRegex(ValueError, "sequence"):
            decode_packet(body + f"|CRC32={checksum}\n".encode("ascii"))

    def test_rejects_non_ascii_wrong_types_and_oversized_frames(self):
        for payload in ("\N{SNOWMAN}", b"\xff", 1, None, "A" * 513):
            with self.subTest(payload=repr(payload)[:30]):
                with self.assertRaises(ValueError):
                    decode_packet(payload)


if __name__ == "__main__":
    unittest.main()
