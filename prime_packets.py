"""Offline laboratory test packets with corruption checks and no sender authentication.

The ASCII frame is intentionally simple and unencrypted. CRC32 detects accidental
corruption; it does not authenticate a sender. Nothing in this module accesses
radio hardware or transmits a signal.
"""

from datetime import datetime, timezone
import re
import zlib


PACKET_NOTICE = (
    "Local laboratory test packet only. "
    "CRC32 checks corruption; sender identity is not authenticated."
)
MAX_PRIME_COUNT = 64
MAX_SEQUENCE = 999999
_MAX_PACKET_BYTES = 512
_FRAME_PATTERN = re.compile(
    r"ASDR-LAB\|v1\|SEQ=([0-9]{6})\|PRIMES=([0-9]+(?:,[0-9]+)*)"
    r"\|CRC32=([0-9A-F]{8})\n"
)


def _bounded_integer(value: int, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer from 1 through {maximum}.")
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer from 1 through {maximum}.")
    return value


def _first_primes(count: int) -> list[int]:
    primes: list[int] = []
    candidate = 2
    while len(primes) < count:
        is_prime = True
        for prime in primes:
            if prime * prime > candidate:
                break
            if candidate % prime == 0:
                is_prime = False
                break
        if is_prime:
            primes.append(candidate)
        candidate += 1
    return primes


def _crc32(body: bytes) -> str:
    return f"{zlib.crc32(body) & 0xFFFFFFFF:08X}"


def build_packet(sequence: int, prime_count: int) -> dict:
    """Build one unencrypted ASCII test frame without using radio hardware.

    Sequence is 1..999999; prime_count is 1..64. CRC32 covers every byte before
    ``|CRC32=``. ``created_utc`` is generation metadata, not part of the frame.
    """
    _bounded_integer(sequence, "sequence", MAX_SEQUENCE)
    _bounded_integer(prime_count, "prime_count", MAX_PRIME_COUNT)
    prime_text = ",".join(str(prime) for prime in _first_primes(prime_count))
    body = f"ASDR-LAB|v1|SEQ={sequence:06d}|PRIMES={prime_text}".encode("ascii")
    checksum = _crc32(body)
    payload = body + f"|CRC32={checksum}\n".encode("ascii")
    return {
        "sequence": sequence,
        "payload_text": payload.decode("ascii"),
        "payload_hex": payload.hex(),
        "crc32": checksum,
        "prime_count": prime_count,
        "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def decode_packet(payload: str | bytes) -> dict:
    """Validate framing, canonical prime sequence, and CRC32 in an offline frame.

    Returns the decoded prime count and list. A successful CRC check proves only
    internal consistency. It says nothing about a signal or sender's origin.
    Invalid or corrupted packets raise ValueError.
    """
    if isinstance(payload, str):
        try:
            payload_bytes = payload.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("Packet must contain ASCII characters only.") from exc
    elif isinstance(payload, bytes):
        payload_bytes = payload
    else:
        raise ValueError("Packet must be ASCII text or bytes.")

    if len(payload_bytes) > _MAX_PACKET_BYTES:
        raise ValueError("Packet exceeds the maximum test-frame length.")
    try:
        payload_text = payload_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Packet must contain ASCII characters only.") from exc
    match = _FRAME_PATTERN.fullmatch(payload_text)
    if match is None:
        raise ValueError("Invalid ASDR-LAB v1 packet framing.")

    sequence_text, prime_text, checksum = match.groups()
    body = payload_bytes.rsplit(b"|CRC32=", 1)[0]
    if _crc32(body) != checksum:
        raise ValueError("Packet CRC32 mismatch.")

    sequence = _bounded_integer(int(sequence_text), "sequence", MAX_SEQUENCE)
    prime_tokens = prime_text.split(",")
    prime_count = _bounded_integer(len(prime_tokens), "prime_count", MAX_PRIME_COUNT)
    expected_primes = _first_primes(prime_count)
    expected_text = ",".join(str(prime) for prime in expected_primes)
    if prime_text != expected_text:
        raise ValueError("Packet must contain the canonical first N prime numbers.")

    return {
        "sequence": sequence,
        "prime_count": prime_count,
        "primes": expected_primes,
        "payload_text": payload_text,
        "payload_hex": payload_bytes.hex(),
        "crc32": checksum,
    }
